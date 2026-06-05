# Drone-Agent

Doğal dil (Türkçe) ile drone kontrolü: **Ollama LLM** + **MCP sunucusu** + **ArduPilot SITL** simülasyonu.

Kullanıcı terminalden Türkçe komut verir; ajan telemetri okur ve temel uçuş komutlarını (kalkış, hedefe gitme, iniş, mod değiştirme) MCP araçları üzerinden çalıştırır.

**Referans:** [Say the Mission, Execute the Swarm (arXiv:2605.03788)](https://arxiv.org/abs/2605.03788)

---

## Mimari

```text
Terminal (Türkçe komut)
    → agent.py          (Ollama + MCP istemcisi)
        → mcp_server.py   (stdio MCP)
            → pymavlink   (UDP port 14551)
                → ArduPilot SITL (sim_vehicle.py)
```

**Kritik:** `mcp_server.py` `udp:0.0.0.0:14551` dinler. SITL başlatırken MAVLink çıkışı **14551** portuna yönlendirilmelidir (aşağıdaki komut).

---

## Gereksinimler

| Bileşen | Not |
|---------|-----|
| Ubuntu | 22.04 veya 24.04 önerilir |
| Python | 3.10+ (`python3 --version`) |
| Git | Depoyu klonlamak için |
| ArduPilot | `~/ardupilot` (SITL) |
| Ollama | Yerel LLM |
| Model | `qwen2.5:7b` (`agent.py` içinde tanımlı) |

Python paketleri: `pip install -r requirements.txt` → `mcp`, `pymavlink`, `ollama`

---

## 1. ArduPilot kurulumu (Ubuntu, ilk kez)

ArduPilot pip ile kurulmaz. İlk kurulum 30–60 dakika sürebilir.

```bash
sudo apt update
sudo apt install -y git python3-pip python3-dev python3-venv \
  build-essential g++ ccache

cd ~
git clone https://github.com/ArduPilot/ardupilot.git
cd ~/ardupilot
git submodule update --init --recursive

Tools/environment_install/install-prereqs-ubuntu.sh -y
```

Terminali kapatıp açın veya: `. ~/.profile`

```bash
cd ~/ardupilot
./waf configure --board sitl
./waf copter
```

Resmi dokümantasyon: https://ardupilot.org/dev/docs/building-setup-linux.html

---

## 2. Bu projeyi indir

```bash
cd ~
git clone https://github.com/Burakozay23/Drone-Agent.git
cd Drone-Agent

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b
ollama list
```

---

## 4. Çalıştırma (iki terminal)

### Terminal 1 — SITL + UDP 14551

```bash
python3 ~/ardupilot/Tools/autotest/sim_vehicle.py -v ArduCopter --console --map --out udp:127.0.0.1:14551
```

Simülasyon ve harita açılana kadar bekleyin. Bu pencere **açık kalsın**.

### Terminal 2 — Agent

```bash
cd ~/Drone-Agent
source .venv/bin/activate
python3 agent.py
```

`mcp_server.py` agent tarafından otomatik başlatılır; ayrı çalıştırmayın.

Çıkış: agent’ta `quit` veya `çık` — SITL’de **Ctrl+C**.

---

## Örnek komutlar

| Türkçe komut | Etki |
|--------------|------|
| `Durumu söyle` | Telemetri |
| `10 metreye kalk` | Arm + takeoff |
| `İn` | İniş |
| `Modu GUIDED yap` | Mod değişimi |

Goto için önce telemetriden konum okuyun; örnek: hedef enlem/boylam + irtifa (maks. **50 m**, sunucu sınırı).

---

## Dosyalar

| Dosya | Görev |
|-------|--------|
| `agent.py` | Ollama, MCP istemcisi, Türkçe arayüz |
| `mcp_server.py` | MCP araçları, pymavlink |
| `requirements.txt` | Python bağımlılıkları |

### MCP araçları

- `get_telemetry` — konum, irtifa, hız, batarya, mod  
- `arm_and_takeoff` — kalkış (`altitude`)  
- `goto` — hedef GPS  
- `land` — iniş  
- `set_mode` — GUIDED, STABILIZE, LOITER, RTL, LAND  

---

## Sorun giderme

| Belirti | Çözüm |
|---------|--------|
| `HATA: GPS verisi alinamadi` | SITL çalışıyor mu? `--out udp:127.0.0.1:14551` kullanıldı mı? |
| Ollama hatası | `ollama serve` + `ollama pull qwen2.5:7b` |
| Port meşgul | Eski SITL’i Ctrl+C ile kapatın |
| `ss -uln \| grep 14551` | Port dinleniyor mu kontrol edin |

---

## Güvenlik

Yalnızca **simülasyon (SITL)** içindir. Gerçek uçuş için ayrı güvenlik prosedürü gerekir.
