# communicate3.py
import os
import requests #type: ignore
import json
from typing import Optional, List, Dict, Any # Import necessary types

def communicate(key: str, conversation_history: list[dict], model_name: str,
                tools: Optional[List[Dict[str, Any]]] = None,
                tool_choice: Optional[str] = None):
    if not key:
        yield {"type": "error", "message": "OpenAI key not found in .env file"}
        return

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }

    data: Dict[str, Any] = {
        "stream": True,
        "model": model_name,
        "messages": conversation_history
    }

    # Add tools and tool_choice to the data payload if provided
    if tools is not None:
        data["tools"] = tools
    if tool_choice is not None:
        data["tool_choice"] = tool_choice

    try:
        # print(json.dumps(data, indent=2))
        with requests.post(
            "https://api.openai.com/v1/chat/completions",  # OpenAI endpoint
            headers=headers,
            json=data,
            stream=True
        ) as r:
            r.raise_for_status()
            buffer = ""
            # print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!Inside with:!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n {json.dumps(data, indent=2)}")
            for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()

                    if line.startswith("data: "):
                        data_str = line[len("data: "):].strip()
                        if data_str == "[DONE]":
                            yield {"type": "end"}
                            return
                        try:
                            json_data = json.loads(data_str)
                            choices = json_data.get("choices", [])
                            if choices and isinstance(choices, list) and len(choices) > 0:
                                delta = choices[0].get("delta", {})

                                # Handle tool calls (OpenAI format)
                                if "tool_calls" in delta:
                                    for tc in delta["tool_calls"]:
                                        if tc.get("type") == "function":
                                            yield {
                                                "type": "tool_call",
                                                "tool_call": {
                                                    "id": tc.get("id"),
                                                    "function": {
                                                        "name": tc["function"].get("name"),
                                                        "arguments": tc["function"].get("arguments", "")
                                                    }
                                                }
                                            }

                                # Handle text content
                                if "content" in delta:
                                    yield {"type": "text", "token": delta["content"]}
                        except json.JSONDecodeError:
                            continue
                    elif line.startswith(":"):
                        continue
            yield {"type": "end"}
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with OpenAI: {e}")
        yield {"type": "error", "message": f"Failed to connect to AI model: {e}"}
    except Exception as e:
        print(f"Unexpected error occurred during OpenAI streaming: {e}")
        yield {"type": "error", "message": f"An unexpected AI error occurred: {e}"}