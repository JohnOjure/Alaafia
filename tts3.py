from elevenlabs.client import ElevenLabs # type: ignore
from elevenlabs import stream # type: ignore
from io import BytesIO
import os
from dotenv import load_dotenv #type: ignore
import uuid

load_dotenv()
eleven_key = os.getenv("eleven_labs_key")

client = ElevenLabs(
    api_key = eleven_key,
)


def text_to_voice(text, voice_id, model_id, client):
    # client = ElevenLabs(
    # api_key = key,
    # )

    # audio_buffer = BytesIO()

    audio_stream = client.text_to_speech.stream(
        text = text,
        voice_id = voice_id,
        model_id = model_id,
        output_format = "mp3_22050_32"
    )

    for chunk in audio_stream:
        if isinstance(chunk, bytes):
            # audio_buffer.write(chunk)
            yield chunk
    
    # audio_buffer.seek(0)
    # return audio_buffer


#voice id for ololade Z8dg0fyk7p6js7cQ7lgi
#voice id for taiwo RAVWJW17BPoSIf05iXxf
#eleven_multilingual_v2
#eleven_turbo_v2_5

if __name__ == "__main__":
    text = "This is streamed audio from ElevenLabs. What do you think? By the way I'm a medical and health assistant named Psi, ask me whatever you want and I'll answer to the best of my ability."
    with open(f"{uuid.uuid4()}.mp3", "wb") as f:
        for chunk in text_to_voice(text, "RAVWJW17BPoSIf05iXxf", "eleven_turbo_v2_5", client):
            f.write(chunk)
