"""Read the answer from a Bedrock Agents InvokeAgent event stream."""

import json


def read_agent_answer(response, logger):
    chunks = []
    for event in response["completion"]:
        if "chunk" in event:
            chunks.append(event["chunk"]["bytes"])
        elif "trace" in event:
            # Botocore decodes trace timestamps to datetime objects.
            logger.info("%s", json.dumps(event["trace"], indent=2, default=str))
        else:
            raise ValueError(f"Unexpected Bedrock event: {list(event)}")
    if not chunks:
        raise ValueError("Bedrock returned no answer chunks.")
    return b"".join(chunks).decode("utf-8")
