import os
import requests #type: ignore
from pinecone import Pinecone #type: ignore
from typing import List, Dict, Any


curacel_health_key = os.getenv("curacel_health_key")
curacel_grow_key = os.getenv("curacel_grow_key")
health_base_url = os.getenv("health_base_url")
grow_base_url = os.getenv("grow_base_url")

def get_response_frontend(llm_key: str, pc: Pinecone, p_host: str, conversation_history: List[Dict[str, str]], user_message: str, use_rag: bool, model_name: str):
    
    if use_rag and user_message.strip():
        try:
            index = pc.Index(host=p_host)
            results = index.search(
                namespace="__default__",
                query={
                    "inputs": {"text": f"{user_message}"},
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
                user_message.insert(1, rag_context_message)
        except Exception as e:
            print(f"Error during Pinecone RAG search: {e}")


    if not llm_key:
        return "I don't have access to my key, lemme try to find it"
    
    headers = {
        "Authorization": f"Bearer {llm_key}",
        "Content-Type": "application/json"
    }

    data: Dict[str, Any] = {
        "stream": False,
        "model": model_name,
        "messages": conversation_history
    }  

    try:
        response_from_aisha = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            stream=False
        )
        print(f"\nResponse from AiSHA in get_response_function: \n{response_from_aisha}")
    
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with OpenAI: {e}")
        return "I couldn't communiucate with OpenAI"
    
    except Exception as e:
        print(f"Unexpected error occurred during OpenAI streaming: {e}")
        return "Oh no,... something went wrong"

