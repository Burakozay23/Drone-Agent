# agent.py
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import ollama

MODEL = "qwen2.5:7b"

SYSTEM_PROMPT = """Sen deneyimli bir drone operatörü asistanısın.
Görevin, kullanıcının doğal dil komutlarını anlayarak
uygun drone araçlarını çağırmak ve sonuçları Türkçe raporlamaktır.

## Elindeki Araçlar

### get_telemetry()
Drone'un anlık durumunu döndürür:
- GPS koordinatları (enlem/boylam)
- Zemine göre irtifa (metre)
- Kuzey/doğu yönlü hız (m/s)
- Batarya durumu (yüzde)
- Mevcut uçuş modu

### arm_and_takeoff(altitude: float)
Drone'u arm eder ve belirtilen irtifaya kaldırır.
- altitude: Hedef irtifa, metre cinsinden (örnek: 10.0)
- Maksimum güvenlik sınırı: 50 metre
- Kullanım: Kullanıcı "kalk", "havalandır", "kaldır" gibi komutlar verdiğinde

### goto(lat: float, lon: float, altitude: float)
Drone'u belirtilen GPS koordinatına uçurur.
- lat: Hedef enlem (örnek: -35.363262)
- lon: Hedef boylam (örnek: 149.165237)
- altitude: Uçuş irtifası, metre cinsinden
- Maksimum güvenlik sınırı: 50 metre

### land()
Drone'u bulunduğu konuma indirir.
- Parametre gerekmez
- Kullanım: Kullanıcı "in", "indir", "land" dediğinde

### set_mode(mode: str)
Drone'un uçuş modunu değiştirir.
- Geçerli modlar: GUIDED, STABILIZE, LOITER, RTL, LAND

## Davranış Kuralları
1. Konum veya durum sorularında ÖNCE get_telemetry() çağır, sonra yanıtla.
2. Uçuş komutlarından önce mevcut durumu get_telemetry() ile kontrol et.
3. Araç sonucunda HATA: ile başlayan mesaj görürsen kullanıcıya bildir, tahmin yapma.
4. Koordinat verileri gerçek MAVLink verisinden gelir — asla uydurma.
5. Tüm yanıtları Türkçe ver.
6. Sayısal parametreleri daima float olarak gönder (örnek: 10.0).
7. Sadece Türkçe yaz, başka dil karakteri kullanma."""


async def run_agent():
    """
    Drone agent ana döngüsü.

    MCP sunucusuna bağlanır, araçları keşfeder ve
    kullanıcı komutlarını Ollama LLM aracılığıyla işler.
    """
    print("DEBUG: Agent fonksiyonu başladı, sunucuya bağlanmaya çalışıyorum...")

    server_params = StdioServerParameters(
        command="python3",
        args=["mcp_server.py"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

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

            print(f"Drone Agent hazır! ({len(tools)} araç yüklendi) Çıkmak için 'quit' yaz.\n")
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

            while True:
                user_input = input("Sen: ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("quit", "exit", "çık"):
                    break

                messages.append({"role": "user", "content": user_input})

                # Araç çağrısı döngüsü — LLM araç döndürmediği ana kadar çalışır
                while True:
                    try:
                        response = ollama.chat(
                            model=MODEL,
                            messages=messages,
                            tools=tools
                        )
                    except Exception as e:
                        print(f"[Ollama hatası]: {e}")
                        messages.pop()
                        print("Agent: Bir hata oluştu, lütfen tekrar deneyin.\n")
                        break

                    # Ollama object olarak döner, dict değil
                    msg = response.message

                    # Tool çağrısı yok → kullanıcıya yanıt ver ve iç döngüden çık
                    if not msg.tool_calls:
                        answer = msg.content or ""
                        messages.append({"role": "assistant", "content": answer})
                        print(f"Agent: {answer}\n")
                        break

                    # Tool çağrısı var → işle
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": msg.tool_calls
                    })

                    for tc in msg.tool_calls:
                        tool_name = tc.function.name
                        tool_args = dict(tc.function.arguments) if tc.function.arguments else {}

                        # get_telemetry parametresiz çalışır, LLM ne gönderirse temizle
                        if tool_name == "get_telemetry":
                            tool_args = {}

                        # String gelen sayısal parametreleri float'a çevir
                        for key, val in tool_args.items():
                            try:
                                tool_args[key] = float(val)
                            except (ValueError, TypeError):
                                pass

                        print(f"[Araç çağrısı: {tool_name}({tool_args})]")

                        result = await session.call_tool(tool_name, tool_args)
                        tool_output = result.content[0].text if result.content else "Sonuç alınamadı."

                        print(f"[Araç sonucu]: {tool_output}")

                        messages.append({
                            "role": "tool",
                            "name": tool_name,
                            "content": tool_output
                        })
                    # İç döngü devam eder — LLM sonuçları değerlendirip
                    # ya yeni tool çağırır ya da kullanıcıya yanıt verir


if __name__ == "__main__":
    asyncio.run(run_agent())
