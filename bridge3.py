"""
Aiit so I've placed alot of comments in this code and the other
files so you'll understand the gist of what's supping. Everything's self-explanatory
buh if you have any questions, lemme know. 

This file binds everything together and wraps the logic in an API

"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, UploadFile, File, HTTPException, status #type: ignore
from fastapi.middleware.cors import CORSMiddleware #type: ignore
from twilio.twiml.voice_response import VoiceResponse, Connect, ConversationRelay #type: ignore
from pinecone import Pinecone # type: ignore
from pydantic import BaseModel #type: ignore
from PyPDF2 import PdfReader # type: ignore
import os
import json
import time
from typing import Dict, List, Any, Optional
import asyncio 
from dotenv import load_dotenv #type: ignore
from datetime import datetime 
import get_response3
import get_response_frontend

load_dotenv()

model_name = os.getenv("model_name") 
ngrok_url = os.getenv("ngrok_url")
open_ai_key = os.getenv("open_ai_key")
# open_ai_key = os.getenv("open_ai_key")
pinecone_key = os.getenv("pinecone_key")
pinecone_host = os.getenv("pinecone_host")
eleven_labs_key = os.getenv("eleven_labs_key")
twilio_sid = os.getenv("twilio_sid")
twilio_auth_token = os.getenv("twilio_auth_token")

print(f"OpenAI Key: {'*'*10}{open_ai_key[-4:] if open_ai_key else 'MISSING'}")
print(f"Model Name: {model_name}")

#dials
index_name = "psi-index"
voice_id = os.getenv("voice_id") 
tts_model = "eleven_turbo_v2_5"
use_rag = False


try:
    #ceating a pinecone client
    pc = Pinecone(api_key=pinecone_key)
except Exception as e:
    print(f"Error creating pinecone client: {e}")


app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  #alows all origins for development. CHANGE THIS FOR PRODUCTION!
    allow_credentials=True,
    allow_methods=["*"],  #allows all methods (GET, POST, etc.)
    allow_headers=["*"],  #allows all headers
)

#conversation manager for Twilio and ConversationRelay vWebsockets
class ConversationManager: 
    def __init__(self):
        #dictionary to store conversation ids and their corresponding websocket connections
        self.active_conversations: Dict[str, WebSocket] = {}

        #stores conversation history for each SID for LLm conversation context
        self.conversation_histories: Dict[str, List[Dict[str, str]]] = {}

        self.current_llm_full_response: Dict[str, str] = {} #stores the the full, intended LLM response for the current turn which might be interrupted  by the user
        self.interruption_events: Dict[str, asyncio.Event] = {} #Dictionary of Call SIDs to interrupt_signal flags to indicate if the current llm speech was interrupted by the user (indicates is the llm was interrupted by the user)

    async def connect_conversation(self, websocket: WebSocket, call_sid: str):
        # await websocket.accept()
        self.active_conversations[call_sid] = websocket

        #initialize conversation history with system prompt for Psi
        self.conversation_histories[call_sid] = [{"role": "system", "content": "You are Psi, a helpful and empathetic health and medical chatbot. Your goal is to provide general health information, answer medical questions, and guide users to appropriate resources. Keep your responses concise and natural for a voice conversation, typically around 2-3 sentences. When asking follow-up questions, make them clear and direct."}]
        self.current_llm_full_response[call_sid] = "" #initialize the current llm full response for this call_sid
        self.interruption_events[call_sid] = asyncio.Event() #initialize the interruption event for this call_sid
        print(f"ConversationRelay Websocket connected for Call SID: {call_sid}")
    
    async def disconnect_conversation(self, call_sid: str):
        if call_sid in self.active_conversations:
           
            try:
                await self.active_conversations[call_sid].close()
            except RuntimeError:
                pass #websocket might already be closed

            del self.active_conversations[call_sid]
            del self.conversation_histories[call_sid]
            if call_sid in self.current_llm_full_response:
                del self.current_llm_full_response[call_sid]
            if call_sid in self.interruption_events:
                del self.interruption_events[call_sid]
            print(f"ConversationRelay Websocket disconnected for Call SID: {call_sid}")
                
    async def send_llm_token_to_twilio(self, call_sid: str, token: str, last: bool = False):
        #sends the text response from the llm back to twilio over the ConversationRelay websocket for tts
        if call_sid in self.active_conversations:
            message = {
                "type": "text",
                "token": token,
                "last": last
                # "streamSid": call_sid
            }
            try:
                await self.active_conversations[call_sid].send_json(message)
                # print(f"Sent token to twilio: {token}")
            except Exception as e:
                print(f"Error sending token message to Twilio: {e}")
                await self.disconnect_conversation(call_sid)
        else:
            print(f"There's no active conversation for Call SID: {call_sid}")

    def set_interrupted(self, call_sid: str): #set's the interruption flag for a given Call SID when the user interrupts Psi's speech
        if call_sid in self.interruption_events:
            self.interruption_events[call_sid].set()
            print(f"Set interruption flag for Call SID: {call_sid}")
    
    def clear_interrupted(self, call_sid: str): #clears the interruption flag 
        if call_sid in self.interruption_events:
            self.interruption_events[call_sid].clear()
            print(f"Cleared interruption flag for Call SID: {call_sid}")

    def is_interrupted(self, call_sid: str) -> bool: #checks if the current llm speech was interrupted by the user
        if call_sid in self.interruption_events:
            return self.interruption_events[call_sid].is_set() #returns True if the interruption flag is set, False otherwise
        return False

#define the expected structure of data from a filed claim (or user input for claim)
class ClaimDetails(BaseModel):
    claim_description: str
    encounter_date: str # YYYY-MM-DD
    enrollee_first_name: str
    enrollee_last_name: str
    enrollee_insurance_no: str
    diagnoses_names: List[str] = []
    service_items_descriptions: List[str] = []
    amount_billed: float = 0.0
    #add an optional field if clinic name is usually extracted from chat/user input
    clinic_name_from_chat: str = None

#define the expected structure of data extracted from a receipt
class ReceiptDetails(BaseModel):
    raw_text: str
    receipt_date: str # YYYY-MM-DD
    total_amount: float
    currency: str
    service_items: List[str]
    clinic_name: str
    patient_name: str = None #patient name might not always be on receipt

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    user_message: str
    conversation_history: Optional[List[Dict[str, str]]] = []


class ChatResponse(BaseModel):
    psi_response: str


manager = ConversationManager()

#the fun begins here...finally
#to those reading through this, one donut!!!

@app.get("/") #endpoint for server status check
async def read_root():
    return {"message": "Aiit, Psi is running..."}

@app.post("/voice") #endpoint that is webhooked (iono if that's a real term) to Psi's twilio number. Returns the TwiML that initiates the ConversationRelay websocket to /ws/conversation at the conversation endpoint 
async def handle_incoming_call(request: Request):
    public_websocket_url = ngrok_url
    if not public_websocket_url:
        print("ngrok url env variable not set")
        public_websocket_url = "wss://localhost:8000"
        print(f"Using fallback websocket url: {public_websocket_url}. Twilio can't connect from outside")
    
    full_websocket_url = public_websocket_url.replace("http://", "wss://").replace("https://", "wss://") + "/ws/conversation" #incase you reading are confused, wss is the protocol for websockets (not http/https)
    #above would be the url would be in the TwiML and tell twilio the endpoint with which it should initiate the ConverationRelay
    response = VoiceResponse()
    connect = Connect()

    conversation_relay = connect.conversation_relay(
        url = full_websocket_url,
        welcomeGreeting = "Hello there, I am Aisha. How can I help you today?",
        welcomeGreetingInterruptible = "speech", # ***
        language = "en-US",
        ttsProvider = "ElevenLabs",
        voice = voice_id,
        transcriptionProvider = "Deepgram",
        speechModel = "nova-3",
        interruptible = "speech",
        reportInputDuringAgentSpeech = "speech", #***if Psi can take user input while it's speaking
        preemptible = True, #***if the current talk cycle of Psi can be interrupted
    )

    response.append(connect)
    print(f"Responding to Twilio with TwiMl: {str(response)}")
    return Response(content = str(response), media_type = "application/xml")

@app.websocket("/ws/conversation") #endpoint where a the websocket connection for converation relay is established
async def conversation_relay_websocket(websocket: WebSocket):
    #receives sst from twilio, pipes it to our llm then tts the response back to twilio
    call_sid: str = None

    if not open_ai_key:
        print("LLM key not in env file")
        await websocket.accept()
        await websocket.send_json({"type": "text", "text": "I'm so sorry, I cannot access my knowledge base. My API key is missing.", "last": True})
        await websocket.close()
        return

    if not pc or not pinecone_host or not index_name:
        print("One or more Pinecone variables are missing...")
        await websocket.accept()
        await websocket.send_json({"type": "text", "text": "I'm sorry, I cannot access my knowledge base. There's a configuration issue.", "last": True})
        await websocket.close()
        return

    try:
        #this initial accept is needed to establish the websocket
        #manager.connect_conversation will re-accept after getting callSid
        #the initial accept is needed for twilio to start sending events
        await websocket.accept()

        while True: #once we accept the connection we now enter a loop where we listen for incoming messages from twilio
            message = await websocket.receive_json()
            message_type = message.get("type")

            if message_type == "setup": #if the message is a setup message (first message twilio sends when the websocket connection is established)
                call_sid = message["callSid"] #call_sid is passed as one of the attributes in the setup json
                await manager.connect_conversation(websocket, call_sid)
                print(f"Conversation started for Call SID: {call_sid}")

            elif message_type == "prompt":  #ConversationRelay sends this message when the caller says something and it has transcribed it
                #remember the flow... User->says something->Twilio(SST)->transcribed text as a message back to our api->LLM->response->Twilio(TTS)->audio to the user
                
                transcribed_text = message["voicePrompt"]
                print(f"User said (from 'prompt' message): {transcribed_text}")
                
                if transcribed_text.strip(): #process only if it's not empty

                    manager.clear_interrupted(call_sid) #clear the interruption flag for this call_sid since the user has just started a new turn
                    
                    #add user's message to conversation history for context
                    manager.conversation_histories[call_sid].append({"role": "user", "content": transcribed_text})
                    print(f"Calling get_response.py with: '{transcribed_text}'")

                    #pipe the user's message to the llm
                    #this might be a lil confusing buh the thing is that get_response.py calls communicate.py which uses requests.post which is synchronous. There we've got to run it in a separate thread using 'asyncio.to_thread' to prevent blocking FastAPI's async event loop
                    
                    time.sleep(1.5) #introduce a little delay to avoid hitting the request limit of Openrouter
                    
                    llm_token_generator = await asyncio.to_thread(
                        get_response3.get_response,
                        llm_key = open_ai_key,
                        pc = pc,
                        p_host = pinecone_host,
                        conversation_history = manager.conversation_histories[call_sid],
                        # u_input = transcribed_text,
                        # index_name = index_name,
                        use_rag = use_rag,
                        model_name = model_name
                    )

                    full_llm_response_intended = "" #to accumulate the full llm response for history
                    error_during_stream = False #to track if an error occurred during the llm streaming
                    
                    #iterate over the llm response tokens
                    try:
                        for token in llm_token_generator:
                            full_llm_response_intended += token #accumulate the llm response
                            
                            if token.startswith("Error:"):
                                print(f"LLM/RAG stream returned an error: {token}")
                                await manager.send_llm_token_to_twilio(call_sid, "I'm sorry, I encountered an issue. Please say that again.", last=True)
                                error_during_stream = True
                                break

                            if manager.is_interrupted(call_sid):
                                print(f"User interrupted Psi's speech for Call SID: {call_sid}. Stopping llm response streaming.")
                                break #stop streaming if the user interrupted Psi's speech

                            await manager.send_llm_token_to_twilio(call_sid, token, last=False) 

                        #only send 'last=True' if not interrupted and no error occurred during streaming
                        if not manager.is_interrupted(call_sid) and not error_during_stream:
                            await manager.send_llm_token_to_twilio(call_sid, "", last = True)
                            print(f"Signaled end of LLM response stream to Twilio for Call SID: {call_sid}")
                        
                        elif manager.is_interrupted(call_sid):
                            print(f"LLM stream finished for Call SID: {call_sid}, but was interrupted.")
                        
                        elif error_during_stream:
                            print(f"LLM stream finished for Call SID: {call_sid} with an error.")
                    
                    except Exception as e:
                        print(f"Error during llm response streaming for Call SID {call_sid}: {e}")
                        await manager.send_llm_token_to_twilio(call_sid, "I'm sorry, I encountered an issue. Please say that again.", last=True)
                        error_during_stream = True

                    #update the conversation history with the full llm response 
                    if error_during_stream:
                        #if an error occured during streaming, revert the last user message to avoid confusion
                        if manager.conversation_histories[call_sid] and manager.conversation_histories[call_sid][-1]["role"] == "user":
                            manager.conversation_histories[call_sid].pop()
                    elif full_llm_response_intended: #if the llm response was successful
                        manager.conversation_histories[call_sid].append({"role": "assistant", "content": full_llm_response_intended})
                        print(f"Added ful intended llm response to history for Call SID: {call_sid}: '{full_llm_response_intended}'")
                    else:
                        print("LLM response was empty or None.")
                        if manager.conversation_histories[call_sid] and manager.conversation_histories[call_sid][-1]["role"] == "user":
                            manager.conversation_histories[call_sid].pop() #remove the last user message to avoid confusion
                        await manager.send_llm_token_to_twilio(call_sid, "I don't think I got what you said. Please try again.", last=True)

                else:
                    print("Twilio sent empty transcription (user paused or no speech detected).")
            
            elif message_type == "dtmf":
                digit = message["digit"]
                print(f"Received a DTMF digit: {digit}")

            elif message_type == "interrupt": #ConversationRelay sends this message when the user interrupts what Psi was saying
                #the speech that Psi 'wanted' to say that was interrupted is already in the conversation history so we need to replace it with what Psi was 'able' to say
                interrupted_utterance = message.get("utteranceUntilInterrupt", "")
                duration_ms = message.get("durationUntilInterruptMs", 0)
                print(f"Received 'interrupt' event. User interrupted after Psi spoke: '{interrupted_utterance}' for {duration_ms}ms.")

                manager.set_interrupted(call_sid) #signal and set the interruption flag for this call_sid so Psi stops streaming the current llm response

                conversation_history = manager.conversation_histories.get(call_sid)
                
                if conversation_history:
                    #check if the last message from the assistant
                    if conversation_history and conversation_history[-1]["role"] == "assistant":
                        # original_full_response = conversation_history[-1]["content"]
                        #replace the last message with the interrupted one
                        #this ensures futures llm turns are based on actally delivered info
                        conversation_history[-1]["content"] = interrupted_utterance
                        print(f"Updated history: Psi's last response truncated to: '{interrupted_utterance}'")
                    else:
                        print("Interrupt received but last message was not from assistant or history is empty.")
                else:
                    print(f"Warning: No conversation history found for Call SID {call_sid} on interrupt.")

            elif message_type == "error":
                print(f"Error message received from Twilio: {message["description"]}")
            else:
                print(f"Received unknown message: {message_type}: {message}")  

    except WebSocketDisconnect:
        if call_sid:
            await manager.disconnect_conversation(call_sid)
        print(f"ConversationRelay Websocket disconnected unexpectedly for Call SID: {call_sid}")
    
    except Exception as e:
        print(f"An unexpected error ocurred in ConversationRelay Websocket for Call SID {call_sid}: {e}")
        
        if call_sid:
            await manager.disconnect_conversation(call_sid)
        try: 
            await manager.send_llm_token_to_twilio(call_sid, "I'm sorry, I've encountered a critical error. Please try calling again.", last = True)
        except Exception as send_e:
            print(f"Failed to send critical error message to Twilio: {send_e}")

#INTERACTS WITH THE WEB APP
@app.post("/parse-receipt") #endpoint to parse and extract receipt data 
async def parse_receipt(file: UploadFile = File(...)):
    """
    Parses an uploaded PDF receipt to extract relevant medical claim information
    using LLM-powered extraction.
    """
    if file.content_type != "application/pdf":
        return {"status": "error", "message": "Only PDF files are accepted."}

    extracted_text = ""
    try:
        # from PyPDF2 import PdfReader
        reader = PdfReader(file.file)
        for page in reader.pages:
            extracted_text += page.extract_text() or ""
        
        if not extracted_text.strip():
            return {"status": "error", "message": "Could not extract any text from the PDF."}

        # --- Use LLM for Data Extraction ---
        # Call the new asynchronous helper function from get_response3.py
        llm_extraction_result = await get_response3.get_llm_extracted_receipt_data(
            raw_text=extracted_text,
            llm_key=open_ai_key, # Using your OpenRouter key for the LLM
            model_name=model_name # Your OpenRouter base URL
        )
        
        if llm_extraction_result["status"] == "success":
            return {
                "status": "success",
                "message": "Receipt parsed and data extracted by AI successfully.",
                "extracted_data": llm_extraction_result["extracted_data"]
            }
        else:
            return {
                "status": "error",
                "message": f"AI extraction failed: {llm_extraction_result['message']}",
                "raw_text": extracted_text # Optionally return raw text for debugging
            }

    except Exception as e:
        print(f"Error processing PDF or during LLM extraction: {e}")
        return {"status": "error", "message": f"An unexpected error occurred during PDF processing: {e}"}

#INTERACTS WITH THE WEB APP
@app.post("/check-fraud") #endpoint for fraud shield
async def check_fraud(
    claim: ClaimDetails,
    receipt: ReceiptDetails
):
    """
    Compares details from a filed claim (or user input) with extracted receipt data
    to identify potential inconsistencies (fraud indicators).
    """
    discrepancies = []

    #date Comparison
    try:
        claim_date_dt = datetime.strptime(claim.encounter_date, "%Y-%m-%d")
        receipt_date_dt = datetime.strptime(receipt.receipt_date, "%Y-%m-%d")

        #allow for a small discrepancy (e.g., within 3 days)
        date_tolerance_days = 3 
        date_difference = abs((claim_date_dt - receipt_date_dt).days)

        if date_difference > date_tolerance_days:
            discrepancies.append({
                "type": "Date Mismatch",
                "message": (f"Claim date ({claim.encounter_date}) differs significantly "
                            f"from receipt date ({receipt.receipt_date}). Difference: {date_difference} days."),
                "severity": "High"
            })
    except ValueError as e:
        discrepancies.append({
            "type": "Date Format Error",
            "message": f"Could not parse date: {e}. Ensure YYYY-MM-DD format.",
            "severity": "Error"
        })

    #amount comparison
    #allow for a percentage tolerance (e.g., 5% difference)
    amount_tolerance_percent = 5.0
    if claim.amount_billed > 0 and receipt.total_amount > 0:
        difference = abs(claim.amount_billed - receipt.total_amount)
        if claim.amount_billed != 0: 
            percentage_difference = (difference / claim.amount_billed) * 100
            if percentage_difference > amount_tolerance_percent:
                discrepancies.append({
                    "type": "Amount Mismatch",
                    "message": (f"Claim amount (₦{claim.amount_billed:,.2f}) differs significantly "
                                f"from receipt total (₦{receipt.total_amount:,.2f}). "
                                f"Difference: {percentage_difference:.2f}%"),
                    "severity": "Medium"
                })
        else: #claim amount is 0, but receipt has an amount
             if receipt.total_amount > 0:
                 discrepancies.append({
                    "type": "Amount Mismatch",
                    "message": (f"Claim amount is ₦0.00, but receipt shows ₦{receipt.total_amount:,.2f}. "
                                "Possible missing claim amount."),
                    "severity": "Medium"
                })


    #clinic ame comparison (basic string matching for mvp)
    #this is a simple exact match. For a real system, you'd use fuzzy matching
    #libraries (like 'fuzzywuzzy' or 'rapidfuzz') for variations like "Faith Clinic Inc." vs "Faith Clinic".
    if claim.clinic_name_from_chat and receipt.clinic_name:
        if claim.clinic_name_from_chat.lower() != receipt.clinic_name.lower():
            discrepancies.append({
                "type": "Clinic Name Mismatch",
                "message": (f"Clinic name from claim/chat ('{claim.clinic_name_from_chat}') "
                            f"does not match receipt ('{receipt.clinic_name}')."),
                "severity": "Low" # Can be elevated based on confidence
            })
    elif claim.clinic_name_from_chat and not receipt.clinic_name:
         discrepancies.append({
                "type": "Clinic Name Missing on Receipt",
                "message": f"Clinic name '{claim.clinic_name_from_chat}' mentioned in chat, but not found on receipt.",
                "severity": "Low"
            })
    elif not claim.clinic_name_from_chat and receipt.clinic_name:
         discrepancies.append({
                "type": "Clinic Name Missing in Chat",
                "message": f"Clinic name '{receipt.clinic_name}' found on receipt, but not mentioned in chat.",
                "severity": "Low"
            })

    #service items comparison (conceptual for mvp)
    #this is complex and would involve nlp similarity or matching against a known service list.
    #for mvp, we can just note if either is empty when the other is not.
    if claim.service_items_descriptions and not receipt.service_items:
        discrepancies.append({
            "type": "Service Items Mismatch",
            "message": "Service items specified in claim/chat but not found on receipt.",
            "severity": "Low"
        })
    elif not claim.service_items_descriptions and receipt.service_items:
        discrepancies.append({
            "type": "Service Items Mismatch",
            "message": "Service items found on receipt but not specified in claim/chat.",
            "severity": "Low"
        })
    #more advanced: check for overlap or significant differences in actual item descriptions
    #for instance: if "malaria test" is in claim services, is "malaria test" or similar in receipt services?
    #this would require more sophisticated string/semantic similarity algorithms.

    if not discrepancies:
        return {
            "status": "success",
            "message": "No significant discrepancies found between claim and receipt data."
        }
    else:
        return {
            "status": "warning",
            "message": "Discrepancies found that may indicate a need for further review.",
            "discrepancies": discrepancies
        }

@app.post("/api/chat", response_model=ChatResponse)
async def chat_with_psi(request: ChatRequest):
    print("Received chat request from frontend.")
    """
    Allows a user to chat with AiSHA via a standard HTTP POST request.
    The frontend sends the current user message and the full conversation history.
    Psi processes the request, potentially uses RAG or tools, and returns a complete text response.
    """
    user_message = request.user_message
    conversation_history = request.conversation_history # Frontend sends the accumulated history

    if not user_message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User message cannot be empty."
        )

    #add the current user message to the history for this turn's processing
    #note: get_response expects "user" and "assistant" roles.
    #the history sent from frontend should already include the user's latest message.
    #so, we just use the history as provided by the frontend.
    #ensure the frontend correctly formats its history like:
    #[{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}, {"role": "user", "content": "How are you?"}]

    # model_name = "gpt-4o"
    use_rag = True if pc else False #only use rag if pinecone is successfully initialized

    full_psi_response = ""
    try:
        #get_response is an async generator. We need to iterate through it
        #to get the full response since we're not using WebSockets.
        #this means the API will wait until get_response yields its final output.
        async for chunk in get_response3.get_response(
            llm_key=open_ai_key,
            pc=pc,
            p_host=pinecone_host,
            conversation_history=conversation_history, # Pass history as provided by frontend
            use_rag=use_rag,
            model_name=model_name
        ):
            if chunk["type"] == "text":
                full_psi_response += chunk["token"]
            elif chunk["type"] == "tool_call":
                #if a tool call occurs, get_response handles it internally
                #and makes a second LLM call. The final text will then follow.
                #we can log this on the backend if needed.
                print(f"Backend: Psi initiated tool call: {chunk['function_name']} with args: {chunk['arguments']}")
            elif chunk["type"] == "error":
                error_detail = chunk.get("message", "An unknown error occurred during Psi's response.")
                print(f"Backend: Error from Psi's generation: {error_detail}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Psi encountered an error: {error_detail}"
                )
            elif chunk["type"] == "end":
                break
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unhandled error during Psi response generation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected server error occurred: {e}"
        )
    # After getting the full response, return it
    # The frontend will be responsible for adding this response to its history
    return ChatResponse(psi_response=full_psi_response)

    # After getting the full response, return it
    # The frontend will be responsible for adding this response to its history
   
@app.post("/chat-aisha", response_model = ChatResponse)
async def chat_with_aisha(request: ChatRequest):
    print("Received a request to chat with AiSHA from the frontend")

    user_message = request.user_message
    conversation_history = request.conversation_history # Frontend sends the accumulated history
    print(f"The user's message from the frontend: {user_message}\n\nThe conversation history: {conversation_history}")
    
    if not user_message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User message cannot be empty."
        )
    
    try:
        aisha_response = get_response_frontend.get_response_frontend(
            llm_key=open_ai_key,
            pc=pc,
            p_host=pinecone_host,
            conversation_history=conversation_history, # Pass history as provided by frontend
            user_message=user_message,
            use_rag=use_rag,
            model_name=model_name
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Unhandled error during Psi response generation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected server error occurred: {e}"
        )
    #after getting the full response, return it
    #the frontend will be responsible for adding this response to its history
    return ChatResponse(psi_response=aisha_response)
