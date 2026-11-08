// Browser transport for the RSDKv4 2-player VS networking.
// Replaces the UDP/asio code in Networking.cpp when building with Emscripten.
// It keeps the same packet format; web/net.js carries the packets between the two players
// (directly with WebRTC, or through a relay server).
#include "RetroEngine.hpp"
#if RETRO_USE_NETWORKING && defined(__EMSCRIPTEN__)

#include <emscripten.h>
#include <cstring>
#include <memory>

char networkHost[64];
char networkGame[7] = "SONIC2";
int networkPort     = 50;
int dcError         = 0;
float lastPing      = 0;
bool waitingForPing = false;

static_assert(4 + sizeof(Entity) <= sizeof(ServerPacket::data), "an Entity no longer fits in one packet");

// ---- JavaScript side: web/net.js (window.RSDKNet) does the real work ----
// It either connects the two players directly (WebRTC) or talks to a relay server.
EM_JS(void, js_net_open, (), {
    if (window.RSDKNet) window.RSDKNet.open();
    else if (window.diagLog) diagLog("Multiplayer: net.js is missing from the page");
});

EM_JS(void, js_net_send, (const unsigned char *ptr, int len), {
    if (window.RSDKNet) window.RSDKNet.send(HEAPU8.slice(ptr, ptr + len));
});

EM_JS(int, js_net_poll, (unsigned char *dst, int maxLen), {
    var m = window.RSDKNet ? window.RSDKNet.poll() : null;
    if (!m) return 0;
    var len = m.length < maxLen ? m.length : maxLen;
    HEAPU8.set(m.subarray(0, len), dst);
    return len;
});

EM_JS(int, js_net_state, (), {
    return window.RSDKNet ? window.RSDKNet.state() : 0; // 0 = none, 1 = connecting, 2 = ready, 3 = closed
});

EM_JS(void, js_net_close, (), {
    if (window.RSDKNet) window.RSDKNet.close();
});

static double lastTimeMs = 0;

class NetworkSession
{
public:
    bool running = false;

    uint code    = 0; // our player id, assigned by the relay
    uint partner = 0;
    uint room    = 0;

    ServerPacket read_msg_;

    void start()
    {
        running = true;
        code    = 0;
        partner = 0;
        room    = 0;
        dcError = 0;
        js_net_open();

        ServerPacket sent;
        sent.header = CL_REQUEST_CODE;
        sent.room   = 0x1F2F3F4F;
        transmit(sent);
        lastTimeMs = emscripten_get_now();
    }

    void close()
    {
        running = false;
        js_net_close();
    }

    // same behaviour as the desktop version: stamp our id and room on every packet
    void write(const ServerPacket &msg, bool = false)
    {
        ServerPacket sent(msg);
        sent.player = code;
        sent.room   = room;
        transmit(sent);
    }

    void leave()
    {
        ServerPacket send;
        send.header = CL_LEAVE;
        vsPlaying   = false;
        write(send);
    }

    void handle()
    {
        double now     = emscripten_get_now();
        lastPing       = (float)(now - lastTimeMs);
        lastTimeMs     = now;
        waitingForPing = false;

        if (!code) {
            if (read_msg_.header == SV_CODES && read_msg_.player)
                code = read_msg_.player;
            return;
        }

        switch (read_msg_.header) {
            case SV_CODES: {
                if (vsPlaying)
                    return;
                room = read_msg_.room;
                if (read_msg_.data.multiData.type > 2) {
                    dcError = 3; // room is full
                    leave();
                    return;
                }

                if (read_msg_.data.multiData.type - 1) {
                    // we joined someone else's room
                    partner = *read_msg_.data.multiData.data;
                    Receive2PVSMatchCode(room);
                    return;
                }
                break; // we are hosting: now wait for SV_NEW_PLAYER
            }
            case SV_NEW_PLAYER: {
                if (partner)
                    return;
                vsPlayerID = 0;
                partner    = read_msg_.player;
                Receive2PVSMatchCode(room);
                return;
            }
            case SV_DATA_VERIFIED:
            // fallthrough
            case SV_DATA: {
                Receive2PVSData(&read_msg_.data.multiData);
                return;
            }
            case SV_NO_ROOM: {
                leave();
                dcError = 5; // room doesn't exist
                return;
            }
            case SV_LEAVE: {
                if (read_msg_.player != partner)
                    return;
                leave();
                dcError = 1; // other player left
                return;
            }
            default: return; // RECEIVED / VERIFY_CLEAR are not needed: WebSockets are reliable
        }
    }

private:
    // Packets are 4 KB in the original format but almost empty, so only send what's used.
    void transmit(ServerPacket &pkt)
    {
        StrCopy(pkt.game, networkGame);
        int len = 16 + 8;
        if (pkt.header == CL_DATA || pkt.header == CL_DATA_VERIFIED) {
            switch (pkt.data.multiData.type) {
                case 1: len = 16 + 4 + (int)sizeof(Entity); break;
                case 2: len = 16 + 12; break;
                default: len = 16 + 8; break;
            }
        }
        if (len > (int)sizeof(ServerPacket))
            len = (int)sizeof(ServerPacket);
        js_net_send((const unsigned char *)&pkt, len);
    }
};

std::shared_ptr<NetworkSession> session;

void InitNetwork() { session = std::make_shared<NetworkSession>(); }

void RunNetwork()
{
    if (!session)
        InitNetwork();
    if (session->running) {
        DisconnectNetwork();
        InitNetwork();
    }
    session->start();
}

void SendData(bool verify)
{
    if (!session || !session->running)
        return;
    ServerPacket send;
    send.header         = CL_DATA + verify;
    send.data.multiData = multiplayerDataOUT;
    session->write(send, verify);
}

void DisconnectNetwork(bool finalClose)
{
    if (session && session->running) {
        session->leave();
        session->close();
    }
    if (finalClose)
        session.reset();
}

void SendServerPacket(ServerPacket &send, bool repeat)
{
    if (session)
        session->write(send, repeat);
}
int GetRoomCode() { return session ? (int)session->room : 0; }
void SetRoomCode(int code)
{
    if (session)
        session->room = code;
}

void SetNetworkGameName(int *a1, const char *name) { StrCopy(networkGame, name); }

// Called once per browser frame (from the main loop): hand incoming packets to the game.
void NetworkWebPoll()
{
    std::shared_ptr<NetworkSession> s = session;
    if (!s || !s->running)
        return;

    for (int guard = 0; guard < 64 && s->running; ++guard) {
        memset((void *)&s->read_msg_, 0, sizeof(ServerPacket));
        int len = js_net_poll((unsigned char *)&s->read_msg_, (int)sizeof(ServerPacket));
        if (len <= 0)
            break;
        s->handle();
    }

    // connection lost or refused
    if (s->running && js_net_state() == 3) {
        s->running = false;
        if (vsPlaying) {
            dcError   = 2; // timed out / dropped mid-match
            vsPlaying = false;
        }
    }
}

#endif
