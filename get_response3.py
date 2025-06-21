import communicate3
import json
import requests #type: ignore
import os 
from pinecone import Pinecone #type: ignore
from typing import List, Dict, Any, Generator
import uuid 
import asyncio
import json
from typing import List, Dict, AsyncGenerator
import communicate3
from pinecone import Pinecone 

# Assuming these are defined elsewhere and correctly imported
# from pinecone import Pinecone 
# from . import communicate3 
# from .tools import CURACEL_TOOLS, file_health_claim, recommend_curacel_policy

curacel_health_key = os.getenv("curacel_health_key")
curacel_grow_key = os.getenv("curacel_grow_key")
health_base_url = os.getenv("health_base_url")
grow_base_url = os.getenv("grow_base_url")
# url = "https://openrouter.ai/api/v1/chat/completions"

#

#define the tools the LLM can use
CURACEL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "file_health_claim",
            "description": "Files a health insurance claim with Curacel. This function is used when the user clearly expresses an intent to file a claim for a health issue or injury, providing details about the incident, the date, and the patient's information including their insurance number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "claim_description": {
                        "type": "string",
                        "description": "A detailed textual description of the health issue, injury, or incident for which the claim is being filed. This serves as the main narrative for the claim. E.g., 'severe back pain after farm work', 'twisted ankle playing football', 'malaria symptoms with fever and headache'."
                    },
                    "encounter_date": {
                        "type": "string",
                        "description": "The exact date when the patient visited the provider or the incident occurred, in YYYY-MM-DD format. If the user uses relative terms like 'today' or 'yesterday', try to convert to a specific date."
                    },
                    "enrollee_first_name": {
                        "type": "string",
                        "description": "The first name of the enrollee (patient) for whom the claim is being filed."
                    },
                    "enrollee_last_name": {
                        "type": "string",
                        "description": "The last name of the enrollee (patient) for whom the claim is being filed."
                    },
                    "enrollee_insurance_no": { # Made required to prompt LLM for it
                        "type": "string",
                        "description": "The insurance number of the enrollee (patient) for whom the claim is being filed. This is a crucial identifier."
                    },
                    "diagnoses_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "An optional array of text descriptions of the diagnoses related to the claim. E.g., ['malaria', 'sprained ankle']. Collect multiple if specified."
                    },
                    "service_items_descriptions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "An optional array of brief descriptions of the services or treatments received. E.g., ['consultation', 'pain medication', 'X-ray']. Collect multiple if specified."
                    },
                    "amount_billed": {
                        "type": "number",
                        "description": "The total amount of money billed for the claim. This is an optional field."
                    }
                },
                "required": ["claim_description", "encounter_date", "enrollee_first_name", "enrollee_last_name", "enrollee_insurance_no"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_curacel_policy",
            "description": "Recommends suitable Curacel insurance policies based on a user's health condition, life event, or general insurance needs. Can filter by insurance type or country.",
            "parameters": {
                "type": "object",
                "properties": {
                    "insurance_type": {
                        "type": "string",
                        "description": "The type of insurance product to recommend, e.g., 'health', 'auto', 'life', 'travel'. If not specified, the AI will try to infer or recommend generally."
                    },
                    "health_condition": {
                        "type": "string",
                        "description": "A brief description of the user's health condition or life event for which they seek coverage, e.g., 'back pain', 'pregnancy', 'requiring eye surgery'. This helps in finding relevant policies."
                    },
                    "country": {
                        "type": "string",
                        "description": "The country for which the insurance policy is needed (ISO alpha-2 code if possible, e.g., 'NG' for Nigeria). Useful for location-specific policies like travel or certain health plans."
                    }
                },
                "required": [] #no hard requirements, LLM can try to infer
            }
        }
    },{
        "type": "function",
        "function": {
            "name": "extract_receipt_data",
            "description": "Extracts structured medical claim information (like date, total amount, service items, clinic name, patient name) from unstructured text, typically from a scanned receipt or document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "receipt_date": {
                        "type": "string",
                        "description": "The date of service or purchase on the receipt, in YYYY-MM-DD format. If only month/day/year are available, infer the current year or the most likely recent year."
                    },
                    "total_amount": {
                        "type": "number",
                        "description": "The total monetary amount billed on the receipt."
                    },
                    "currency": {
                        "type": "string",
                        "description": "The currency of the total amount, e.g., 'NGN', 'USD', 'EUR'."
                    },
                    "service_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "A list of individual services, medications, or items detailed on the receipt. Each item should be a brief description."
                    },
                    "clinic_name": {
                        "type": "string",
                        "description": "The name of the clinic, hospital, or pharmacy that issued the receipt."
                    },
                    "patient_name": {
                        "type": "string",
                        "description": "The name of the patient (if mentioned) on the receipt."
                    }
                },
                "required": ["receipt_date", "total_amount", "currency", "service_items", "clinic_name"]   
            }
        }
    }
]

async def get_llm_extracted_receipt_data(raw_text: str, llm_key: str, llm_url: str) -> Dict[str, Any]:
    """
    Uses the LLM's tool-calling capability to extract structured data from raw text.
    """
    if not llm_key or not llm_url:
        return {"status": "error", "message": "LLM key or URL not configured for extraction."}

    #define a temporary tool set containing only the extraction tool
    extraction_tools = [
        next(tool for tool in CURACEL_TOOLS if tool["function"]["name"] == "extract_receipt_data")
    ]

    messages = [
        {"role": "system", "content": "You are a highly skilled data extraction assistant. Your task is to accurately extract specified information from the provided text using the `extract_receipt_data` tool. Do not generate any conversational text; only call the tool with the extracted data. If a piece of information is explicitly not found, omit it from the tool call or use a placeholder like 'N/A' if required by the schema."},
        {"role": "user", "content": f"Please extract the medical receipt data from the following text:\n\n{raw_text}\n\nStrictly use the `extract_receipt_data` tool for this."}
    ]

    try:
        llm_response_generator = communicate3.communicate(
            llm_key,
            messages,
            llm_url, 
            tools=extraction_tools,
            tool_choice="auto" #ensure LLM prefers to use a tool if applicable
        )

        #iterate through the generator to find the tool call
        for chunk in llm_response_generator:
            if chunk["type"] == "tool_call":
                tool_call = chunk["tool_call"]
                if tool_call["function"]["name"] == "extract_receipt_data":
                    try:
                        extracted_args = json.loads(tool_call["function"]["arguments"])
                        return {"status": "success", "extracted_data": extracted_args}
                    except json.JSONDecodeError:
                        return {"status": "error", "message": "LLM returned invalid JSON for tool arguments."}
                else:
                    return {"status": "error", "message": f"LLM called unexpected tool: {tool_call['function']['name']}"}
            elif chunk["type"] == "text" and chunk["token"].strip():
                #if LLM returns text instead of tool call, it means it couldn't extract
                print(f"LLM returned text instead of tool call: {chunk['token']}")
                return {"status": "error", "message": f"LLM could not extract data from the receipt. Raw LLM response: {chunk['token']}"}
        
        #if the generator finishes without yielding a tool_call
        return {"status": "error", "message": "LLM did not return a tool call for receipt data extraction."}

    except Exception as e:
        print(f"Error during LLM receipt data extraction: {e}")
        return {"status": "error", "message": f"An unexpected error occurred during LLM extraction: {e}"}


#Curacel API calling functions
def file_health_claim(
    claim_description: str,
    encounter_date: str,
    enrollee_first_name: str,
    enrollee_last_name: str,
    enrollee_insurance_no: str, #made required so the LLM asks a follow-up question if not provided
    diagnoses_names: List[str] = None,
    service_items_descriptions: List[str] = None,
    amount_billed: float = 0.0
) -> str:
    """
    makes an API call to Curacel's Health API to file a health claim
    """

    if not curacel_health_key or not health_base_url:
        return json.dumps({"status": "error", "message": "API key or Health API base url not configured"})

    headers = {
        "Authorization": f"Bearer {curacel_health_key}",
        "Content-Type": "application/json",
    }

    #prepare diagnoses
    diagnoses_payload = {"names": diagnoses_names if diagnoses_names else ["Unspecified diagnosis"]}

    #prepare items - assigning default unit_price_billed and qty for voice MVP
    items_payload = []
    if service_items_descriptions:
        for desc in service_items_descriptions:
            items_payload.append({
                "description": desc,
                "unit_price_billed": 0.0, #default for voice MVP
                "qty": 1 #default for voice MVP
            })
    else:
        items_payload.append({
            "description": "Unspecified service",
            "unit_price_billed": 0.0,
            "qty": 1
        })

    payload = {
        "encounter_date": encounter_date,
        "diagnoses": diagnoses_payload,
        "items": items_payload,
        "enrollee": {
            "insurance_no": enrollee_insurance_no, 
            "first_name": enrollee_first_name,
            "last_name": enrollee_last_name,
            "is_floating": False,
            "create_if_not_found": False #*****
        },
        "description": claim_description, #this is the top-level claim description
        
        # --- PLACEHOLDERS FOR HACKATHON ---
        #these values would typically come from your Curacel integration setup (Insurer/Provider codes)
        #or be determined by context.
        "hmo_code": "HACKATHON_HMO", #replace with actual HMO code
        "provider_code": "HACKATHON_PROVIDER", #replace with actual Provider code
        # --- END PLACEHOLDERS ---
        
        "is_draft": True, #default to true for easy review in Curacel portal
        "ref": str(uuid.uuid4()), # Generate a unique reference ID
        "auto_vet": False,
        "create_missing_tariffs": False,
        "amount_billed": amount_billed,
        "admission_date": None, #optional, setting to None
        "discharge_date": None, #optional, setting to None
        "pa_code": None, #optional, setting to None
        "attachments": [] #optional, no attachments for basic voice claim
    }

    try:
        api_endpoint = f"{health_base_url}v1/claims" 
        print(f"Making POST request to: {api_endpoint}")
        print(f"Payload: {json.dumps(payload, indent=2)}")

        response = requests.post(api_endpoint, headers=headers, json=payload)
        response.raise_for_status()

        response_data = response.json()
        claim_id = response_data.get("id")
        
        if claim_id:
            return json.dumps({"status": "success", "claim_id": claim_id, "message": f"Your health claim for '{claim_description}' has been successfully filed with ID {claim_id}."})
        else:
            return json.dumps({"status": "error", "message": f"Claim filed successfully but no ID returned. Response: {response_data}"})

    except requests.exceptions.HTTPError as http_err:
        error_detail = response.json() if response else "No response"
        print(f"HTTP error occurred: {http_err} - Details: {error_detail}")
        return json.dumps({"status": "error", "message": f"Failed to file claim: API returned an error. Status: {response.status_code}. Details: {error_detail.get('message', error_detail)}"})
    except requests.exceptions.ConnectionError as conn_err:
        print(f"Connection error occurred: {conn_err}")
        return json.dumps({"status": "error", "message": f"Failed to connect to Curacel API. Please check your internet connection or API base URL."})
    except requests.exceptions.Timeout as timeout_err:
        print(f"Timeout error occurred: {timeout_err}")
        return json.dumps({"status": "error", "message": "Curacel API request timed out."})
    except requests.exceptions.RequestException as req_err:
        print(f"An unexpected request error occurred: {req_err}")
        return json.dumps({"status": "error", "message": f"An unknown error occurred while communicating with Curacel API: {req_err}"})
    except Exception as e:
        print(f"An unexpected error occurred during claim filing: {e}")
        return json.dumps({"status": "error", "message": f"An unexpected error occurred: {e}"})

def recommend_curacel_policy(insurance_type: str = "health", health_condition: str = "general", country: str = None) -> str:
    """
    Makes an API call to Curacel's Grow API to list / recommend insurance products.
    """
    if not curacel_grow_key or not grow_base_url:
        return json.dumps({"status": "error", "message": "API key or Grow API base URL not configred"})

    headers = {
        "Authorization": f"Bearer {curacel_grow_key}",
        "Content-Type": "application/json",
    }

    params = {
        "type": insurance_type,
        "q": health_condition #using 'q' to search by health condition keywords
    }
    if country:
        params["insurer_country"] = country

    try:
        api_endpoint = f"{grow_base_url}v1/products" 
        print(f"Making GET request to: {api_endpoint} with params: {params}")

        response = requests.get(api_endpoint, headers=headers, params=params)
        response.raise_for_status() 

        response_data = response.json()
        products = response_data.get("data", []) #grow api returns data in a 'data' array

        if products:
            recommendations_list = []
            for product in products:
                
                #assuming products have 'name', 'description', and 'id' or 'slug' for link #*************
                #Will check the grow api documnetation to see if these are the correct keys #******************************
                product_name = product.get("name", "Unnamed Product")
                product_description = product.get("short_description", "No description available.")
                
                #mock link cos actual buy links might be dynamic
                product_link = f"https://curacel.co/buy/{product.get('slug', product.get('id', 'default'))}" 
                recommendations_list.append({
                    "name": product_name,
                    "description": product_description,
                    "link": product_link
                })

            return json.dumps({"status": "success", "recommendations": recommendations_list, "message": "Here are some policies that might fit your needs:"})
        else:
            return json.dumps({"status": "success", "recommendations": [], "message": "I couldn't find any specific policies matching your request. Please try different keywords or visit our website for more options."})

    except requests.exceptions.HTTPError as http_err:
        error_detail = response.json() if response else "No response"
        print(f"HTTP error occurred: {http_err} - Details: {error_detail}")
        return json.dumps({"status": "error", "message": f"Failed to fetch policies: API returned an error. Status: {response.status_code}. Details: {error_detail.get('message', error_detail)}"})
    except requests.exceptions.ConnectionError as conn_err:
        print(f"Connection error occurred: {conn_err}")
        return json.dumps({"status": "error", "message": f"Failed to connect to Curacel API. Please check your internet connection or API base URL."})
    except requests.exceptions.Timeout as timeout_err:
        print(f"Timeout error occurred: {timeout_err}")
        return json.dumps({"status": "error", "message": "Curacel API request timed out."})
    except requests.exceptions.RequestException as req_err:
        print(f"An unexpected request error occurred: {req_err}")
        return json.dumps({"status": "error", "message": f"An unknown error occurred while communicating with Curacel API: {req_err}"})
    except Exception as e:
        print(f"An unexpected error occurred during policy recommendation: {e}")
        return json.dumps({"status": "error", "message": f"An unexpected error occurred: {e}"})


async def get_response(llm_key: str, pc: Pinecone, p_host: str, conversation_history: List[Dict[str, str]], use_rag: bool, model_name: str) -> AsyncGenerator[Dict, None]:
    """
    Generates a response from the LLM, potentially involving RAG and tool calls.
    Yields dictionary chunks (text, tool_call, error, end).
    """
    current_user_input = conversation_history[-1]["content"]
    messages_for_llm = list(conversation_history)

    # RAG Logic (omitted for brevity, assume it's correct from previous versions)
    if use_rag and current_user_input.strip():
        try:
            index = pc.Index(host=p_host)
            results = index.search(
                namespace="__default__",
                query={
                    "inputs": {"text": f"{current_user_input}"},
                    "top_k": 3
                },
                rerank={
                    "model": "bge-reranker-v2-m3",
                    "top_n": 2,
                    "rank_fields": ["content"]
                },
                fields=["title", "content"]
            )

            contents = [hit['fields']['content'] for hit in results['result']['hits']]
            context_text = "\n\n".join(contents)

            if context_text:
                rag_context_message = {
                    "role": "system",
                    "content": f"Refer to the following context if relevant to the user's query: {context_text}"
                }
                messages_for_llm.insert(0, rag_context_message)
        except Exception as e:
            print(f"Error during Pinecone RAG search: {e}")

    # Now, communicate3.communicate will refer to your actual module's function
    llm_response_generator = communicate3.communicate( # This line remains the same
        llm_key,
        messages_for_llm,
        model_name,
        tools=CURACEL_TOOLS
    )

    full_llm_response_content = ""
    tool_call_made = False

    try:
        async for chunk in llm_response_generator: # This will now iterate over your actual LLM stream
            if chunk["type"] == "text":
                token = chunk["token"]
                full_llm_response_content += token
                yield {"type": "text", "token": token}
            elif chunk["type"] == "tool_call":
                tool_call = chunk["tool_call"]
                tool_call_made = True

                function_name = tool_call["function"]["name"]
                arguments_str = tool_call["function"]["arguments"]

                print(f"LLM requested tool call: {function_name} with args: {arguments_str}")

                try:
                    arguments = json.loads(arguments_str)
                    tool_output = ""

                    if function_name == "file_health_claim":
                        claim_description = arguments.get("claim_description")
                        encounter_date = arguments.get("encounter_date")
                        enrollee_first_name = arguments.get("enrollee_first_name")
                        enrollee_last_name = arguments.get("enrollee_last_name")
                        enrollee_insurance_no = arguments.get("enrollee_insurance_no")
                        diagnoses_names = arguments.get("diagnoses_names", [])
                        service_items_descriptions = arguments.get("service_items_descriptions", [])
                        amount_billed = arguments.get("amount_billed", 0.0)

                        tool_output = file_health_claim(
                            claim_description=claim_description,
                            encounter_date=encounter_date,
                            enrollee_first_name=enrollee_first_name,
                            enrollee_last_name=enrollee_last_name,
                            enrollee_insurance_no=enrollee_insurance_no,
                            diagnoses_names=diagnoses_names,
                            service_items_descriptions=service_items_descriptions,
                            amount_billed=amount_billed
                        )

                    elif function_name == "recommend_curacel_policy":
                        insurance_type = arguments.get("insurance_type")
                        health_condition = arguments.get("health_condition")
                        country = arguments.get("country")

                        tool_output = recommend_curacel_policy(
                            insurance_type=insurance_type,
                            health_condition=health_condition,
                            country=country
                        )
                    else:
                        tool_output = json.dumps({"status": "error", "message": f"Unknown tool: {function_name}"})

                    messages_for_llm.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", "no_id"),
                        "name": function_name,
                        "content": tool_output
                    })

                    second_llm_response_generator = communicate3.communicate(
                        llm_key,
                        messages_for_llm,
                        model_name,
                        tools=CURACEL_TOOLS
                    )

                    second_full_response = ""
                    async for second_chunk in second_llm_response_generator:
                        if second_chunk["type"] == "text":
                            second_token = second_chunk["token"]
                            second_full_response += second_token
                            yield {"type": "text", "token": second_token}
                        elif second_chunk["type"] == "tool_call":
                            print(f"Warning: Chained tool call detected: {second_chunk['tool_call']['function']['name']}. "
                                  "This project iteration handles only one level of tool calls for simplicity.")
                            yield {"type": "text", "token": "I encountered a complex request and need to simplify. Can you rephrase?"}
                            break

                    if second_full_response:
                        messages_for_llm.append({"role": "assistant", "content": second_full_response})

                    yield {"type": "end"}
                    return

                except json.JSONDecodeError:
                    yield {"type": "text", "token": "Error: Failed to parse arguments for the tool call. Can you please provide the details clearly?"}
                    messages_for_llm.append({"role": "assistant", "content": "Error: Failed to parse arguments for the tool call. Can you please provide the details clearly?"})
                    yield {"type": "end"}
                    return
                except Exception as ex:
                    yield {"type": "text", "token": f"Error processing tool request: {ex}. Please try again or describe your issue in more detail."}
                    messages_for_llm.append({"role": "assistant", "content": f"Error processing tool request: {ex}. Please try again or describe your issue in more detail."})
                    yield {"type": "end"}
                    return
    except Exception as e:
        print(f"Error in get_response LLM streaming/tool handling: {e}")
        yield {"type": "error", "message": f"An issue occurred processing your request: {e}"}
        yield {"type": "end"}
        return

    if not tool_call_made and not full_llm_response_content:
        yield {"type": "text", "token": "I'm sorry, I didn't get a clear response. Can you please rephrase?"}

    yield {"type": "end"}

    