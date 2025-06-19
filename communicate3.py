# Communicate.py handles communication with the llm api and returns the response
# stream enabled

import os
import requests #type: ignore                                         
import json
# from openai import OpenAI #type: ignore

def communicate(key: str, url: str, convo_history: list[dict], llm_url: str):
    if not key:
        yield "Key not found in .env file"
        return
    
    api_key = key
    # api_base = base
    # url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    data = {
        "stream": True,
        "model": llm_url,
        "messages": convo_history
    }

    try:
        #still use requests.post with stream = True
        with requests.post(url, headers = headers, json = data, stream = True) as r:
            r.raise_for_status()
            #OpenRouter streams in SSE format (Server-Sent Events)
            buffer = ""
            for chunk in r.iter_content(chunk_size = None, decode_unicode = True):
                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1) #split by first newline
                    line = line.strip()

                    if line.startswith("data: "):
                        data = line[len("data: "):].strip()
                        if data == "[DONE]":
                            break #End of the stream
                        try:
                            json_data = json.loads(data)
                            token = json_data.get("choices", [{}])[0].get("delta", {}).get("content")
                            if token:
                                yield token
                        except json.JSONDecodeError:
                            #Incomplete JSON or other json lines, buffer more
                            continue
                    elif line.startswith(":"): #possible comment from OpenRouter
                        continue
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with OpenRouter: {e}")
        yield f"Failed to connect to AI model: {e}"
    except Exception as e:
        print(f"Unexpected error occured during OpenRouter streaming: {e}")
        yield f"An unexpected AI error occured: {e}"


    # try:      
    #     response = requests.post(url, headers=headers, json=data)
    # except Exception as e:
    #     return f"Something went wrong: {e}"

    # if response.status_code == 200:
    #     return response.json()["choices"][0]["message"]["content"]
    # else:
    #     return f"❌ Error {response.status_code}: {response.text}"

