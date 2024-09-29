import os
import google.generativeai as genai
from dotenv import load_dotenv
import requests
from datetime import datetime
from fastapi import FastAPI
import json
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def determine_travel_year(travel_month):
    """
    Determine the appropriate year for the travel date based on the current date.
    
    :param travel_month: The month of travel as an integer (e.g., 7 for July)
    :return: The year in which the travel should occur
    """
    # Get the current date
    current_date = datetime.now()
    current_month = current_date.month
    current_year = current_date.year
    
    # If the travel month is before or in the current month, assume next year
    if travel_month < current_month or (travel_month == current_month and datetime.now().day > 1):
        return current_year + 1
    else:
        return current_year

load_dotenv()

GEMINI_TOKEN = os.getenv('GEMINI_TOKEN')
FLAB_TOKEN = os.getenv('FLABS_KEY')

genai.configure(api_key=GEMINI_TOKEN)

def get_airport_ids(query):
    url = f"https://www.goflightlabs.com/retrieveAirport?access_key={FLAB_TOKEN}&query={query}"
    response = requests.get(url)
    data = response.json()
    
    if len(data) > 0:
        airport_info = data[0]
        return airport_info.get('entityId'), airport_info.get('skyId')
    else:
        raise ValueError(f"No airport found for query: {query}")

def get_flight_data(origin_sky_id, destination_sky_id, origin_entity_id, destination_entity_id, date):
    url = f"https://www.goflightlabs.com/retrieveFlights?access_key={FLAB_TOKEN}&originSkyId={origin_sky_id}&destinationSkyId={destination_sky_id}&originEntityId={origin_entity_id}&destinationEntityId={destination_entity_id}&date={date}"
    response = requests.get(url)
    return response.json()

def process_flight_data(data):
    processed_flights = []
    
    for itinerary in data['itineraries']:
        for leg in itinerary['legs']:
            # Get the marketing airline name (assuming there's at least one)
            airline_name = leg['carriers']['marketing'][0]['name'] if leg['carriers']['marketing'] else "Unknown Airline"
            
            # Get flightNumber and alternateId from the first segment
            # (assuming there's at least one segment and we're interested in the first one)
            first_segment = leg['segments'][0] if leg['segments'] else {}
            flight_number = first_segment.get('flightNumber', 'N/A')
            alternate_id = first_segment.get('marketingCarrier', {}).get('alternateId', 'N/A')
            
            flight = {
                'price': itinerary['price']['formatted'],
                'from': f"{leg['origin']['city']} ({leg['origin']['displayCode']})",
                'to': f"{leg['destination']['city']} ({leg['destination']['displayCode']})",
                'departure': datetime.fromisoformat(leg['departure']).strftime('%Y-%m-%d %H:%M'),
                'arrival': datetime.fromisoformat(leg['arrival']).strftime('%Y-%m-%d %H:%M'),
                'duration': f"{leg['durationInMinutes'] // 60}h {leg['durationInMinutes'] % 60}m",
                'airline': airline_name,
                'flightNumber': flight_number,
                'alternateId': alternate_id
            }
            processed_flights.append(flight)
    
    return processed_flights

def extract_flight_details(flights_data):
    best_overall = None
    most_economical = None
    shortest = None

    for flight in flights_data.get('itineraries', []):
        tags = flight.get('tags', [])
        
        # Check for the "cheapest" tag
        if 'cheapest' in tags:
            most_economical = flight

        # Check for the "shortest" tag
        if 'shortest' in tags:
            shortest = flight

        # Assuming best overall is determined by the highest score
        if best_overall is None or flight.get('score', 0) > best_overall.get('score', 0):
            best_overall = flight

    return best_overall, most_economical, shortest

    # Extract relevant details for each flight
    def extract_details(flight):
        if not flight:
            return None
        
        legs = flight.get('legs', [])
        return {
            'price': flight.get('price', {}).get('formatted'),
            'score': flight.get('score'),
            'origin': legs[0].get('origin', {}).get('city') if legs else None,
            'destination': legs[-1].get('destination', {}).get('city') if legs else None,
            'departure': legs[0].get('departure') if legs else None,
            'arrival': legs[-1].get('arrival') if legs else None,
            'duration': sum(leg.get('durationInMinutes', 0) for leg in legs),
            'stops': sum(leg.get('stopCount', 0) for leg in legs),
            'airline': legs[0].get('carriers', {}).get('marketing', [{}])[0].get('name') if legs else None
        }


model = genai.GenerativeModel('gemini-1.5-flash')

def generate_trip_plan(user_input):

    expected_schema = {
        "type": "object",
        "properties": {
            "origin": {
                "type": "string"
            },
            "destination": {
                "type": "string"
            },
            "travel_month": {
                "type": "integer",
                "minimum": 1,
                "maximum": 12
            }
        },
        "required": ["origin", "destination", "travel_month"]
    }

    # Parse user input (this is a placeholder; actual parsing logic will depend on input format)
    response = model.generate_content(f"""<|im_start|>system
You are a helpful assistant that answers in JSON. Here's the json schema you must adhere to:\n<schema>\n{expected_schema}\n</schema><|im_end|>
JUST GIVE THE JSON AS OUTPUT. ABSOLUTELY NOTHING ELSE. like nothing else. it should start with an opening curly brace and end with a closing curly brace
if somethiung is not json seriasiable or null or something pick a random value lmao. If the source location is not specified by the user assume it is Phoenix by default and also Autocorrect if there's any type, for example if someone types mombai you should be able to understand they meant mumbai.
Also read the user input properly and see if the user's travel destination does not have the airport then get the nearest airport to the destination.
 {user_input}""")
    
    user_input = json.loads(response.text)

    # Extract relevant details from user input
    travel_month = user_input.get('travel_month')
    origin_query = user_input.get('origin')
    destination_query = user_input.get('destination')
    
    # Determine the correct year for travel
    travel_year = determine_travel_year(travel_month)
    
    # Format the full travel date (assuming day 1 of the month for simplicity)
    travel_date = f"{travel_year}-{travel_month:02d}-01"
    
    # Debugging statement to verify correct date

    # Retrieve entityId and skyId for origin and destination
    origin_entity_id, origin_sky_id = get_airport_ids(origin_query)
    destination_entity_id, destination_sky_id = get_airport_ids(destination_query)

    # Fetch flight data
    flights_data = get_flight_data(origin_sky_id, destination_sky_id, origin_entity_id, destination_entity_id, travel_date)

    # Extract relevant flight details
    best_overall, most_economical, shortest = extract_flight_details(flights_data)

    # Format the trip plan using Google Gemini
    print(best_overall)
    prompt = f"""
    Plan a trip based on the following information:
    - Origin: {origin_query}
    - Destination: {destination_query}
    Make sure there is a section in the itinerary with flight section with 3 options displayed to the the user in case that Airline is the best option.
    If the user wants to go to some place via driving or train or bus, make sure to include the best option for that as well.

    Best Overall Flight: {best_overall}
    
    Most Economical Flight: {most_economical}
    
    Shortest Flight: {shortest}
    
    Please provide a detailed day-to-day itinerary including:
    
    - Suggested flights or transportation
    - Hotel recommendations
    - Daily activities and attractions
    - Estimated costs
    - Local transportation options
    
    Format the response in a clear, easy-to-read structure.
    """
    
    response = model.generate_content(prompt)
    return {
        'flight_data': process_flight_data(flights_data),
        'response': response.text
    }

# user_input = "I want to plan a 7-day trip to Tokyo for 2 people in July with a budget of $3000."
# trip_plan = generate_trip_plan(user_input)
# print(trip_plan)

@app.get("/")
def read_root():
    return {"Hello": "World"}

@app.get("/trip_plan")
def read_trip_plan(query: str):
    return generate_trip_plan(query)

if __name__ == "__main__":   
    import uvicorn
    uvicorn.run("gemini_orbit:app", host="0.0.0.0", port=8000, reload=True)