"""
Drone MCP Server Module.

Provides a Model Context Protocol (MCP) server for an autonomous drone agent.
Communicates with ArduPilot SITL via MAVLink and publishes a W3C Web of Things (WoT)
compliant Thing Description (TD) to enable LLM-based agents to autonomously discover
the device and adhere to hardware limits.
"""

import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from pymavlink import mavutil

app = Server("drone-mcp-server")

# W3C Thing Description baseline defining the identity, properties, and actions of the drone.
THING_DESCRIPTION_BASE = {
    "@context": "https://www.w3.org/2022/wot/td/v1.1",
    "id": "urn:dev:drone:uav-1",
    "title": "Otonom Gözlem Dronu (UAV-1)",
    "description": "MCP üzerinden kontrol edilebilen, MAVLink tabanlı sanal otonom ajan.",
    "securityDefinitions": {
        "nosec_sc": {
            "scheme": "nosec"
        }
    },
    "security": "nosec_sc",
    "properties": {
        "telemetry": {
            "title": "Anlık Telemetri Verisi",
            "description": "Drone'un konum, hız, batarya ve uçuş modu bilgilerini içeren JSON nesnesi.",
            "type": "object",
            "readOnly": True,
            "properties": {
                "lat": {"type": "number", "description": "Enlem (Latitude)"},
                "lon": {"type": "number", "description": "Boylam (Longitude)"},
                "relative_alt": {"type": "number", "description": "Zemine göre irtifa (metre)"},
                "vx": {"type": "number", "description": "Kuzey yönlü hız (m/s)"},
                "vy": {"type": "number", "description": "Doğu yönlü hız (m/s)"},
                "battery": {"type": "integer", "description": "Kalan batarya yüzdesi"},
                "mode": {"type": "string", "description": "Mevcut uçuş modu"},
                "armed": {"type": "boolean", "description": "Motorların kilitli olma durumu (ARM)"}
            },
            "forms": [
                {
                    "href": "drone://uav-1/telemetry",
                    "op": ["readproperty"],
                    "contentType": "application/json"
                }
            ]
        },
        "statusSummary": {
            "title": "Durum Özeti",
            "description": "Drone'un insan ve LLM tarafından hızlıca okunabilir metin formatındaki özeti.",
            "type": "string",
            "readOnly": True,
            "forms": [
                {
                    "href": "drone://uav-1/status",
                    "op": ["readproperty"],
                    "contentType": "text/plain"
                }
            ]
        }
    },
    "actions": {
        "arm_and_takeoff": {
            "title": "Motorları Çalıştır ve Kalkış Yap",
            "description": "Drone motorlarını güvenli bir şekilde çalıştırır (arm) ve belirtilen hedef irtifaya dikey kalkış yaptırır.",
            "input": {
                "type": "object",
                "properties": {
                    "altitude": {
                        "type": "number",
                        "minimum": 1.0,
                        "maximum": 50.0,
                        "description": "Metre cinsinden kalkış yüksekliği."
                    }
                },
                "required": ["altitude"]
            },
            "forms": [
                {
                    "href": "mcp://tools/arm_and_takeoff",
                    "op": ["invokeaction"],
                    "contentType": "application/json"
                }
            ]
        },
        "goto": {
            "title": "Koordinata Git",
            "description": "Drone'u havayken belirtilen küresel GPS koordinatına ve irtifaya yönlendirir.",
            "input": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Hedef enlem (Latitude) derecesi."},
                    "lon": {"type": "number", "description": "Hedef boylam (Longitude) derecesi."},
                    "altitude": {
                        "type": "number",
                        "minimum": 1.0,
                        "maximum": 50.0,
                        "description": "Metre cinsinden hedef uçuş yüksekliği."
                    }
                },
                "required": ["lat", "lon", "altitude"]
            },
            "forms": [
                {
                    "href": "mcp://tools/goto",
                    "op": ["invokeaction"],
                    "contentType": "application/json"
                }
            ]
        },
        "land": {
            "title": "İniş Yap",
            "description": "Drone'un o an bulunduğu konuma dikey ve güvenli şekilde iniş yapmasını sağlar.",
            "forms": [
                {
                    "href": "mcp://tools/land",
                    "op": ["invokeaction"]
                }
            ]
        },
        "set_mode": {
            "title": "Uçuş Modunu Değiştir",
            "description": "Drone'un otopilot uçuş modunu günceller.",
            "input": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["GUIDED", "STABILIZE", "LOITER", "RTL", "LAND"],
                        "description": "Seçilmek istenen geçerli otopilot modu."
                    }
                },
                "required": ["mode"]
            },
            "forms": [
                {
                    "href": "mcp://tools/set_mode",
                    "op": ["invokeaction"]
                }
            ]
        }
    }
}

# Global cache to hold the latest telemetry data.
_telemetry_cache = {
    "lat": None, "lon": None,
    "alt": None, "relative_alt": None,
    "vx": None, "vy": None,
    "battery": None, "mode": None,
    "armed": False,
    "last_update": None
}

# Persistent MAVLink connection used exclusively by the telemetry updater.
_mav_connection = None


def get_connection():
    """
    Creates and returns a new MAVLink UDP connection for general use.

    Returns:
        mavutil.mavlink_connection: A connected MAVLink instance waiting for heartbeat.
    """
    mav = mavutil.mavlink_connection(
        'udp:0.0.0.0:14551', input=True, source_system=255
    )
    mav.wait_heartbeat(timeout=10)
    return mav


def get_writer():
    """
    Creates and returns a temporary MAVLink connection specifically for sending commands.

    Returns:
        mavutil.mavlink_connection: A connected MAVLink instance ready for command execution.
    """
    mav = mavutil.mavlink_connection(
        'udp:0.0.0.0:14551', input=True, source_system=255
    )
    mav.wait_heartbeat(timeout=10)
    return mav


async def telemetry_updater():
    """
    Background task that continuously polls MAVLink telemetry and updates the global cache.

    Uses an executor to prevent blocking the main asyncio event loop while reading
    from the synchronous pymavlink interface.
    """
    global _mav_connection
    import time

    loop = asyncio.get_event_loop()

    def connect_and_read():
        mav = mavutil.mavlink_connection(
            'udp:0.0.0.0:14551', input=True, source_system=255
        )
        mav.wait_heartbeat(timeout=10)
        return mav

    _mav_connection = await loop.run_in_executor(None, connect_and_read)
    print("[Telemetri] MAVLink bağlantısı kuruldu, güncelleme başladı.")

    while True:
        def read_messages():
            import time
            msgs = {}
            t = time.time()
            while time.time() - t < 0.5:
                msg = _mav_connection.recv_match(blocking=False)
                if msg:
                    msgs[msg.get_type()] = msg
            return msgs

        msgs = await loop.run_in_executor(None, read_messages)

        if 'GLOBAL_POSITION_INT' in msgs:
            pos = msgs['GLOBAL_POSITION_INT']
            _telemetry_cache['lat'] = pos.lat / 1e7
            _telemetry_cache['lon'] = pos.lon / 1e7
            _telemetry_cache['alt'] = pos.alt / 1000
            _telemetry_cache['relative_alt'] = pos.relative_alt / 1000
            _telemetry_cache['vx'] = pos.vx / 100
            _telemetry_cache['vy'] = pos.vy / 100

        if 'BATTERY_STATUS' in msgs:
            bat = msgs['BATTERY_STATUS']
            _telemetry_cache['battery'] = bat.battery_remaining

        if 'HEARTBEAT' in msgs:
            hb = msgs['HEARTBEAT']
            _telemetry_cache['mode'] = mavutil.mode_string_v10(hb)
            _telemetry_cache['armed'] = bool(
                hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            )

        import time
        _telemetry_cache['last_update'] = time.time()
        await asyncio.sleep(0.1)


@app.list_resources()
async def list_resources() -> list[types.Resource]:
    """
    Registers the available resources exposed by the MCP server.

    Returns:
        list[types.Resource]: A list of available resources including TD, telemetry, and status.
    """
    return [
        types.Resource(
            uri="drone://uav-1/telemetry",
            name="UAV-1 Anlık Telemetri",
            description=(
                "Drone'un sürekli güncellenen anlık durumu: "
                "GPS konumu, irtifa, hız, batarya, mod, arm durumu."
            ),
            mimeType="application/json"
        ),
        types.Resource(
            uri="drone://uav-1/status",
            name="UAV-1 Durum Özeti",
            description="Drone'un insan tarafından okunabilir durum özeti.",
            mimeType="text/plain"
        ),
        types.Resource(
            uri="drone://uav-1/thing-description",
            name="UAV-1 Thing Description",
            description="W3C WoT standardına uygun dron yetenek, limit ve tanım şeması.",
            mimeType="application/td+json"
        )
    ]


@app.read_resource()
async def read_resource(uri: str) -> str:
    """
    Handles read requests for MCP resources.

    Args:
        uri (str): The requested resource URI.

    Returns:
        str: The content of the requested resource (JSON or Plain Text).
    """
    if uri == "drone://uav-1/thing-description":
        return json.dumps(THING_DESCRIPTION_BASE, ensure_ascii=False, indent=2)

    if uri == "drone://uav-1/telemetry":
        return json.dumps(_telemetry_cache, ensure_ascii=False, indent=2)

    if uri == "drone://uav-1/status":
        c = _telemetry_cache
        if c['lat'] is None:
            return "Telemetri verisi henüz alınamadı."
        armed_str = "ARM" if c['armed'] else "DISARM"
        return (
            f"Konum   : lat={c['lat']:.6f}, lon={c['lon']:.6f}\n"
            f"İrtifa  : {c['relative_alt']:.1f}m (zemine göre)\n"
            f"Hız     : {c['vx']:.1f}m/s kuzey, {c['vy']:.1f}m/s doğu\n"
            f"Batarya : {c['battery']}%\n"
            f"Mod     : {c['mode']}\n"
            f"Durum   : {armed_str}"
        )

    return f"HATA: Bilinmeyen kaynak URI: {uri}"


def _do_arm_and_takeoff(altitude):
    """
    Executes the blocking MAVLink sequence for arming and taking off.

    Args:
        altitude (float): The target altitude in meters.

    Returns:
        bool: True if the target altitude was reached within 30 seconds, False otherwise.
    """
    import time
    mav = get_writer()
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
        if _telemetry_cache['relative_alt'] is not None:
            if _telemetry_cache['relative_alt'] >= altitude * 0.90:
                reached = True
                break
        time.sleep(0.5)
    mav.close()
    return reached


async def tool_get_telemetry(args: dict) -> str:
    """
    Returns the current drone state formatted from the local telemetry cache.

    Args:
        args (dict): Tool arguments (expected to be empty).

    Returns:
        str: Human-readable telemetry summary.
    """
    c = _telemetry_cache
    if c['lat'] is None:
        return "HATA: Telemetri verisi henüz hazır degil."
    armed_str = "ARM" if c['armed'] else "DISARM"
    return (
        f"konum   : lat={c['lat']:.6f}, lon={c['lon']:.6f}\n"
        f"irtifa  : {c['relative_alt']:.1f}m (zemine gore)\n"
        f"hiz     : {c['vx']:.1f}m/s kuzey, {c['vy']:.1f}m/s dogu\n"
        f"batarya : {c['battery']}%\n"
        f"mod     : {c['mode']}\n"
        f"durum   : {armed_str}"
    )


async def tool_arm_and_takeoff(args: dict) -> str:
    """
    Initiates the arm and takeoff sequence, validating against TD maximum limits.

    Args:
        args (dict): Must contain 'altitude' (float).

    Returns:
        str: Execution status message.
    """
    altitude = float(args["altitude"])
    
    # Retrieve the safety limit directly from the Thing Description
    max_limit = THING_DESCRIPTION_BASE["actions"]["arm_and_takeoff"]["input"]["properties"]["altitude"]["maximum"]

    if altitude > max_limit:
        return f"HATA: Guvenlik siniri asildi. Maksimum irtifa {max_limit}m."

    loop = asyncio.get_event_loop()
    reached = await loop.run_in_executor(None, _do_arm_and_takeoff, altitude)
    if reached:
        return f"Basarili: Drone {altitude}m irtifaya ulasti."
    return f"Uyari: 30sn icinde {altitude}m hedefine ulasilamadi."


async def tool_goto(args: dict) -> str:
    """
    Sends a guided mode position target to the drone.

    Args:
        args (dict): Must contain 'lat', 'lon', and 'altitude' (floats).

    Returns:
        str: Execution status message.
    """
    lat = float(args["lat"])
    lon = float(args["lon"])
    altitude = float(args["altitude"])

    # Retrieve the safety limit directly from the Thing Description
    max_limit = THING_DESCRIPTION_BASE["actions"]["goto"]["input"]["properties"]["altitude"]["maximum"]

    if altitude > max_limit:
        return f"HATA: Guvenlik siniri asildi. Maksimum irtifa {max_limit}m."

    mav = get_writer()
    mav.mav.send(mavutil.mavlink.MAVLink_set_position_target_global_int_message(
        0, mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,
        int(lat * 1e7), int(lon * 1e7), altitude,
        0, 0, 0, 0, 0, 0, 0, 0
    ))
    mav.close()
    return f"Goto komutu gonderildi: lat={lat}, lon={lon}, alt={altitude}m"


async def tool_land(args: dict) -> str:
    """
    Sets the flight mode to LAND.

    Args:
        args (dict): Tool arguments (expected to be empty).

    Returns:
        str: Execution status message.
    """
    mav = get_writer()
    mav.set_mode('LAND')
    mav.close()
    return "Inis komutu gonderildi."


async def tool_set_mode(args: dict) -> str:
    """
    Changes the autopilot mode to the requested state.

    Args:
        args (dict): Must contain 'mode' (str).

    Returns:
        str: Execution status message.
    """
    mode = args["mode"].upper()
    gecerli = {"GUIDED", "STABILIZE", "LOITER", "RTL", "LAND"}
    if mode not in gecerli:
        return f"HATA: Gecersiz mod. Gecerli modlar: {sorted(gecerli)}"
    mav = get_writer()
    mav.set_mode(mode)
    mav.close()
    return f"Mod degistirildi: {mode}"


TOOL_REGISTRY = {
    "get_telemetry":   tool_get_telemetry,
    "arm_and_takeoff": tool_arm_and_takeoff,
    "goto":            tool_goto,
    "land":            tool_land,
    "set_mode":        tool_set_mode,
}

# Dynamically inject max limits into tool descriptions based on TD configuration
_arm_max = THING_DESCRIPTION_BASE["actions"]["arm_and_takeoff"]["input"]["properties"]["altitude"]["maximum"]
_goto_max = THING_DESCRIPTION_BASE["actions"]["goto"]["input"]["properties"]["altitude"]["maximum"]

TOOL_DEFINITIONS = [
    types.Tool(
        name="get_telemetry",
        description="Drone anlik konum, irtifa, hiz, batarya ve mod bilgisi. Parametre gerekmez.",
        inputSchema={"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    ),
    types.Tool(
        name="arm_and_takeoff",
        description="Drone'u arm eder ve irtifaya kaldirir.",
        inputSchema={
            "type": "object",
            "properties": {
                "altitude": {"type": ["number", "string"], "description": f"Hedef irtifa metre. Maks {_arm_max}."}
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
                "lat":      {"type": ["number", "string"], "description": "Hedef enlem"},
                "lon":      {"type": ["number", "string"], "description": "Hedef boylam"},
                "altitude": {"type": ["number", "string"], "description": f"Irtifa metre. Maks {_goto_max}."}
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
    """
    Registers the available tools exposed by the MCP server.

    Returns:
        list[types.Tool]: A list of available actions the agent can perform.
    """
    return TOOL_DEFINITIONS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """
    Routes an incoming tool execution request to the appropriate handler.

    Args:
        name (str): The name of the tool to invoke.
        arguments (dict): The arguments provided by the client/LLM.

    Returns:
        list[types.TextContent]: The execution result formatted as MCP text content.
    """
    handler = TOOL_REGISTRY.get(name)
    if handler is None:
        return [types.TextContent(type="text", text=f"HATA: '{name}' araci bulunamadi.")]
    try:
        result = await handler(arguments)
    except Exception as e:
        result = f"HATA: {e}"
    return [types.TextContent(type="text", text=result)]


async def main():
    """
    Main entry point. Starts the background telemetry loop and the stdio MCP server.
    """
    asyncio.create_task(telemetry_updater())

    async with stdio_server() as streams:
        await app.run(
            streams[0], streams[1],
            app.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
