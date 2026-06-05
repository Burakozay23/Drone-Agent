import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from pymavlink import mavutil

app = Server("drone-mcp-server")
MAX_ALTITUDE = 50

def get_connection():
    mav = mavutil.mavlink_connection('udp:0.0.0.0:14551', input=True, source_system=255)
    mav.wait_heartbeat(timeout=10)
    return mav

def _do_arm_and_takeoff(altitude):
    import time
    mav = get_connection()
    mav.set_mode('GUIDED')
    time.sleep(2)
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 1, 21196, 0, 0, 0, 0, 0
    )
    time.sleep(2)
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0, 0, 0, 0, 0, 0, 0, altitude
    )
    start = time.time()
    reached = False
    while time.time() - start < 30:
        msg = mav.recv_match(type='GLOBAL_POSITION_INT', blocking=True, timeout=2)
        if msg and (msg.relative_alt / 1000) >= altitude * 0.90:
            reached = True
            break
    mav.close()
    return reached

async def tool_get_telemetry(args: dict) -> str:
    mav = get_connection()
    pos = mav.recv_match(type='GLOBAL_POSITION_INT', blocking=True, timeout=5)
    bat = mav.recv_match(type='BATTERY_STATUS', blocking=True, timeout=3)
    hb = mav.recv_match(type='HEARTBEAT', blocking=True, timeout=3)
    mav.close()
    if not pos:
        return "HATA: GPS verisi alinamadi."
    mode = mavutil.mode_string_v10(hb) if hb else "BILINMIYOR"
    bat_pct = f"{bat.battery_remaining}%" if bat else "?"
    return (
        f"konum   : lat={pos.lat/1e7:.6f}, lon={pos.lon/1e7:.6f}\n"
        f"irtifa  : {pos.relative_alt/1000:.1f}m (zemine gore)\n"
        f"hiz     : {pos.vx/100:.1f}m/s kuzey, {pos.vy/100:.1f}m/s dogu\n"
        f"batarya : {bat_pct}\n"
        f"mod     : {mode}"
    )

async def tool_arm_and_takeoff(args: dict) -> str:
    altitude = float(args["altitude"])
    if altitude > MAX_ALTITUDE:
        return f"HATA: Guvenlik siniri asildi. Maksimum irtifa {MAX_ALTITUDE}m."
    loop = asyncio.get_event_loop()
    reached = await loop.run_in_executor(None, _do_arm_and_takeoff, altitude)
    if reached:
        return f"Basarili: Drone {altitude}m irtifaya ulasti."
    return f"Uyari: 30sn icinde {altitude}m hedefine ulasilamadi."

async def tool_goto(args: dict) -> str:
    lat = float(args["lat"])
    lon = float(args["lon"])
    altitude = float(args["altitude"])
    if altitude > MAX_ALTITUDE:
        return f"HATA: Guvenlik siniri asildi. Maksimum irtifa {MAX_ALTITUDE}m."
    mav = get_connection()
    mav.mav.send(mavutil.mavlink.MAVLink_set_position_target_global_int_message(
        0,
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,
        int(lat * 1e7), int(lon * 1e7), altitude,
        0, 0, 0, 0, 0, 0, 0, 0
    ))
    mav.close()
    return f"Goto komutu gonderildi: lat={lat}, lon={lon}, alt={altitude}m"

async def tool_land(args: dict) -> str:
    mav = get_connection()
    mav.set_mode('LAND')
    mav.close()
    return "Inis komutu gonderildi."

async def tool_set_mode(args: dict) -> str:
    mode = args["mode"].upper()
    gecerli = {"GUIDED", "STABILIZE", "LOITER", "RTL", "LAND"}
    if mode not in gecerli:
        return f"HATA: Gecersiz mod. Gecerli modlar: {sorted(gecerli)}"
    mav = get_connection()
    mav.set_mode(mode)
    mav.close()
    return f"Mod degistirildi: {mode}"

TOOL_REGISTRY = {
    "get_telemetry": tool_get_telemetry,
    "arm_and_takeoff": tool_arm_and_takeoff,
    "goto": tool_goto,
    "land": tool_land,
    "set_mode": tool_set_mode,
}

TOOL_DEFINITIONS = [
    types.Tool(
        name="get_telemetry",
        description="Drone anlik konum, irtifa, hiz ve batarya bilgisi. Parametre gerekmez.",
        inputSchema={"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    ),
    types.Tool(
        name="arm_and_takeoff",
        description="Drone'u arm eder ve irtifaya kaldirir.",
        inputSchema={
            "type": "object",
            "properties": {
                "altitude": {"type": ["number", "string"], "description": "Hedef irtifa metre. Maks 50."}
            },
            "required": ["altitude"]
        }
    ),
    types.Tool(
        name="goto",
        description="Drone'u GPS koordinatina ucurur. Drone havada olmalidir.",
        inputSchema={
            "type": "object",
            "properties": {
                "lat": {"type": ["number", "string"], "description": "Hedef enlem"},
                "lon": {"type": ["number", "string"], "description": "Hedef boylam"},
                "altitude": {"type": ["number", "string"], "description": "Irtifa metre. Maks 50."}
            },
            "required": ["lat", "lon", "altitude"]
        }
    ),
    types.Tool(
        name="land",
        description="Drone'u indirir. Parametre gerekmez.",
        inputSchema={"type": "object", "properties": {}, "required": []}
    ),
    types.Tool(
        name="set_mode",
        description="Ucus modunu degistirir: GUIDED, STABILIZE, LOITER, RTL, LAND",
        inputSchema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "description": "Mod adi"}
            },
            "required": ["mode"]
        }
    ),
]

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOL_DEFINITIONS

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    handler = TOOL_REGISTRY.get(name)
    if handler is None:
        return [types.TextContent(type="text", text=f"HATA: '{name}' araci bulunamadi.")]
    try:
        result = await handler(arguments)
    except Exception as e:
        result = f"HATA: {e}"
    return [types.TextContent(type="text", text=result)]

async def main():
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
