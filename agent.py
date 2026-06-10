"""
Drone Autonomous Agent Module.

This module establishes a bridge between a local LLM (Qwen) and the MCP server.
The agent dynamically discovers its capabilities and hardware limits by reading
the W3C Thing Description (TD) resource upon initialization.
"""

import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import ollama

MODEL = "qwen2.5:7b"
MAX_HISTORY = 15


def generate_system_prompt(td_content: str) -> str:
    """
    Generates a dynamic agent prompt by reading the device's Thing Description data.

    Args:
        td_content (str): W3C Thing Description text in JSON format.

    Returns:
        str: A dynamic and unrestricted system prompt prepared for the LLM.
    """
    return f"""Sen otonom bir drone asistanısın.

Bağlandığın cihazın özellikleri, donanımsal limitleri ve kullanabileceğin
tüm eylemler aşağıdaki W3C Thing Description (TD) JSON verisinde tanımlanmıştır:

{td_content}

YAKLAŞIM VE KURALLAR:
- Kullanıcı hedeflerine ulaşmak için kullanacağın araçları (tools) ve izleyeceğin
  stratejiyi belirlemekte tamamen özgürsün. Durumu analiz edip inisiyatif alabilirsin.
- Eylemlerini planlarken her zaman TD içindeki donanım limitlerine (örn: maximum irtifa) uy.
  Eğer kullanıcının komutu bu limitleri aşıyorsa, cihazın sınırlarını (TD referansıyla)
  açıklayarak kullanıcıyı uyar ve komutu güvenli sınırlar içinde revize ederek uygula.
- Durum farkındalığı yaratmak için elindeki telemetri ve durum araçlarını
  gerektiği zaman otonom olarak kullan.
- Yanıtlarını sadece Türkçe, anlaşılır, net ve profesyonel bir şekilde ver.
"""


async def run_agent():
    """
    Main execution loop for the agent.

    Connects to the MCP server, fetches the hardware identity (TD), constructs
    the system prompt, and translates natural language user commands into tool calls.
    """
    print("DEBUG: Sunucuya bağlanılıyor ve cihaz keşfediliyor...")

    server_params = StdioServerParameters(
        command="python3",
        args=["mcp_server.py"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. Self-Discovery: Read the device's own definition (TD) from the server
            try:
                td_result = await session.read_resource("drone://uav-1/thing-description")
                td_content = td_result.contents[0].text
                system_prompt = generate_system_prompt(td_content)
                print("[Keşif] Thing Description (TD) başarıyla okundu ve entegre edildi.")
            except Exception as e:
                print(f"[Keşif Hatası] TD okunamadı. Sunucu ayarlarını kontrol edin: {e}")
                return

            # 2. Fetch tools dynamically from the server
            tools_result = await session.list_tools()
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.inputSchema
                    }
                }
                for t in tools_result.tools
            ]

            print(f"Drone Agent Hazır! ({len(tools)} araç yüklendi) Çıkmak için 'quit' yaz.\n")
            
            # 3. Initialize message history with the dynamic prompt
            messages = [{"role": "system", "content": system_prompt}]

            while True:
                user_input = input("Sen: ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("quit", "exit", "çık"):
                    break

                messages.append({"role": "user", "content": user_input})

                # Tool execution and evaluation inner loop
                while True:
                    try:
                        response = ollama.chat(
                            model=MODEL,
                            messages=messages,
                            tools=tools
                        )
                    except Exception as e:
                        print(f"[Ollama Hatası]: {e}")
                        messages.pop()  # Revert the faulty command
                        print("Agent: Bir hata oluştu, lütfen tekrar deneyin.\n")
                        break

                    msg = response.message

                    # If the agent didn't use a tool (or finished its task), print the response and break
                    if not msg.tool_calls:
                        answer = msg.content or ""
                        messages.append({"role": "assistant", "content": answer})
                        print(f"Agent: {answer}\n")
                        break

                    # If the agent called one or more tools, append them to the context
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": msg.tool_calls
                    })

                    for tc in msg.tool_calls:
                        tool_name = tc.function.name
                        tool_args = dict(tc.function.arguments) if tc.function.arguments else {}

                        # Sanitize arguments against LLM hallucinations (String -> Float conversion)
                        if tool_name == "get_telemetry":
                            tool_args = {}
                        
                        for key, val in tool_args.items():
                            try:
                                tool_args[key] = float(val)
                            except (ValueError, TypeError):
                                pass

                        print(f"  [Araç Tetiklendi: {tool_name}({tool_args})]")

                        # Execute the tool on the MCP server and retrieve the result
                        result = await session.call_tool(tool_name, tool_args)
                        tool_output = result.content[0].text if result.content else "Sonuç alınamadı."

                        print(f"  [Sensör/Sistem Yanıtı]: {tool_output}")

                        messages.append({
                            "role": "tool",
                            "name": tool_name,
                            "content": tool_output
                        })
                    
                    # --- CONTEXT PROTECTION ---
                    # Prune old messages to prevent context overflow while keeping the initial system prompt
                    if len(messages) > MAX_HISTORY:
                        messages = [messages[0]] + messages[-(MAX_HISTORY-1):]


if __name__ == "__main__":
    asyncio.run(run_agent())
