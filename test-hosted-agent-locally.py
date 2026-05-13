import httpx, json

r = httpx.post(
    "http://localhost:8088/responses",
    json={
        "model": "InternalHRHelper",
        "stream": False,
        "input": [{"role": "user", "content": "Any learning budget?"}],
    },
    timeout=120,
)
data = r.json()

for item in data.get("output", []):
    item_type = item.get("type")

    if item_type == "message":
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                print("=== Assistant ===")
                print(text)
                print()

    elif item_type == "function_call":
        args = item.get("arguments", "")
        try:
            args = json.dumps(json.loads(args), indent=2)
        except (ValueError, TypeError):
            pass
        print(f"=== Tool call: {item.get('name')} ===")
        print(args)
        print()

    elif item_type == "mcp_call":
        args = item.get("arguments", "")
        try:
            args = json.dumps(json.loads(args), indent=2)
        except (ValueError, TypeError):
            pass
        print(f"=== MCP call: {item.get('server_label')}.{item.get('name')} ===")
        print(args)
        print()

    else:
        # Other tool types: web_search_call, code_interpreter_call, file_search_call, etc.
        name = item.get("name") or item.get("server_label") or ""
        print(f"=== Tool call: {item_type} {name} ===")
        print()