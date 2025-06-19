import communicate3

def get_response(llm_key, pc, p_host, conversation_history: list[dict], use_rag, llm_url):
    current_user_input = conversation_history[-1]["content"]
    context_message = {}
    try:
        if use_rag:
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
            context_text = "\n\n".join(contents) #context from rag that the llm wil use to answer the question
            
            if context_text:
                #create new context message to prepend/insert
                context_message = {
                    "role": "system",
                    "content": f"Refer to the following context if relevant: {context_text}"
                }

            #prepare messages list for LLM API call
            #start with the original system prompt from conversation history
            messages_for_llm = [conversation_history[0]]  #the first message is the system prompt

            #add the RAG context prompt (context_message) after the original system prompt
            if context_message:
                messages_for_llm.append(context_message)

            #add the rest of the conversation history (excluding the first system prompt)
            messages_for_llm.extend(conversation_history[1:])

            # messages_for_llm = list(conversation_history) #convert the conversation history to a list
            # original_system_prompt = messages_for_llm[0]["content"]
            # messages_for_llm[0]["content"] = (
            #     original_system_prompt + "\n\nIntegrate the following context into your response if relevant, but do not directly quote it unless necessary: " + context
            # )  #don't new system prompt details keep getting added to the base system prompt every time inference is made??????????????????????????


        else:
            # context = None
            messages_for_llm = list(conversation_history)
        
        return communicate3.communicate(
            llm_key, 
            "https://openrouter.ai/api/v1/chat/completions", 
            messages_for_llm, 
            llm_url
        )
   
    except Exception as e:
        print(f"Error in get_response: {e}")
        return f"Error: An issue occured retrieving information: {e}"


#get_response.py uses communicate.py and returns the complete llm response to the user input.
