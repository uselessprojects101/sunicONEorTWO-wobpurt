"use strict";
const http = require("http");
const crypto = require("crypto");
const { WebSocketServer } = require("ws");

const PORT = parseInt(process.env.PORT || "8787", 10);
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
const MAX_CLIENTS = 2000;
const MAX_ROOMS = 1000;
const MAX_PACKET = 8192;
const MAX_MSGS_PER_SEC = 240;

const CL = { REQUEST_CODE: 0x00, JOIN: 0x01, DATA: 0x10, DATA_VERIFIED: 0x11, QUERY_VERIFICATION: 0x20, LEAVE: 0xff };
const SV = { CODES: 0x00, NEW_PLAYER: 0x01, DATA: 0x10, DATA_VERIFIED: 0x11, NO_ROOM: 0x81, LEAVE: 0xff };
const HELLO_ROOM = 0x1f2f3f4f;
const HEADER = 16;

const clients = new Set();
const byId = new Map();
const rooms = new Map();

function log(...a) { console.log(new Date().toISOString(), ...a); }

function randomId() {
  for (;;) {
    const id = crypto.randomInt(1, 0xffffffff);
    if (!byId.has(id)) return id;
  }
}

function makeCodes(game, playerId, roomCode, type, data0) {
  const b = Buffer.alloc(HEADER + 8);
  b[0] = SV.CODES;
  b.write(game || "", 1, 7, "latin1");
  b.writeUInt32LE(playerId >>> 0, 8);
  b.writeUInt32LE(roomCode >>> 0, 12);
  b.writeInt32LE(type | 0, 16);
  b.writeInt32LE(data0 | 0, 20);
  return b;
}

function makeSimple(header, game, playerId, roomCode) {
  const b = Buffer.alloc(HEADER + 8);
  b[0] = header;
  b.write(game || "", 1, 7, "latin1");
  b.writeUInt32LE(playerId >>> 0, 8);
  b.writeUInt32LE(roomCode >>> 0, 12);
  return b;
}

function send(client, buf) {
  if (client && client.ws.readyState === 1) client.ws.send(buf, { binary: true });
}

function partnerOf(client) {
  const r = client.room ? rooms.get(client.room) : null;
  if (!r) return null;
  return r.host === client ? r.guest : r.host;
}

function leaveRoom(client) {
  if (!client.room) return;
  const r = rooms.get(client.room);
  client.room = 0;
  if (!r) return;
  rooms.delete(r.code);
  const other = r.host === client ? r.guest : r.host;
  if (other) {
    other.room = 0;
    send(other, makeSimple(SV.LEAVE, client.game, client.id, r.code));
  }
  log("room closed", r.code.toString(16).padStart(8, "0"), rooms.size, "open");
}

function newRoomCode(settings) {
  for (let i = 0; i < 100; i++) {
    const code = (((crypto.randomInt(1, 1 << 20)) << 12) | (settings & 0xff0)) >>> 0;
    if (!rooms.has(code)) return code;
  }
  return 0;
}

function onPacket(client, buf) {
  if (buf.length < HEADER) return;
  const header = buf[0];
  const player = buf.readUInt32LE(8);
  const roomField = buf.readUInt32LE(12);
  client.game = buf.toString("latin1", 1, 8).replace(/\0.*$/, "");

  if (header === CL.REQUEST_CODE && roomField === HELLO_ROOM) {
    if (!client.id) {
      client.id = randomId();
      byId.set(client.id, client);
    }
    send(client, makeCodes(client.game, client.id, 0, 0, 0));
    return;
  }

  if (!client.id || player !== client.id) return;

  switch (header) {
    case CL.REQUEST_CODE: {
      if (rooms.size >= MAX_ROOMS) { send(client, makeSimple(SV.NO_ROOM, client.game, client.id, 0)); return; }
      leaveRoom(client);
      const settings = buf.length >= HEADER + 8 ? buf.readInt32LE(HEADER + 4) : 0;
      const code = newRoomCode(settings);
      if (!code) { send(client, makeSimple(SV.NO_ROOM, client.game, client.id, 0)); return; }
      rooms.set(code, { code, host: client, guest: null });
      client.room = code;
      send(client, makeCodes(client.game, client.id, code, 1, 0));
      log("room opened", code.toString(16).padStart(8, "0"), rooms.size, "open");
      return;
    }
    case CL.JOIN: {
      const r = rooms.get(roomField);
      if (!r || r.host === client) { send(client, makeSimple(SV.NO_ROOM, client.game, client.id, roomField)); return; }
      if (r.guest) { send(client, makeCodes(client.game, client.id, roomField, 3, 0)); return; }
      leaveRoom(client);
      r.guest = client;
      client.room = r.code;
      send(client, makeCodes(client.game, client.id, r.code, 2, r.host.id));
      send(r.host, makeSimple(SV.NEW_PLAYER, client.game, client.id, r.code));
      log("room joined", r.code.toString(16).padStart(8, "0"));
      return;
    }
    case CL.DATA:
    case CL.DATA_VERIFIED: {
      const other = partnerOf(client);
      if (!other) return;
      const out = Buffer.from(buf);
      out[0] = header === CL.DATA ? SV.DATA : SV.DATA_VERIFIED;
      out.writeUInt32LE(client.id, 8);
      out.writeUInt32LE(client.room, 12);
      send(other, out);
      return;
    }
    case CL.QUERY_VERIFICATION:
      return;
    case CL.LEAVE:
      leaveRoom(client);
      return;
  }
}

const server = http.createServer((req, res) => {
  res.writeHead(200, { "content-type": "text/plain" });
  res.end("RSDK web relay: " + clients.size + " connected, " + rooms.size + " rooms\n");
});

const wss = new WebSocketServer({
  server,
  maxPayload: MAX_PACKET,
  verifyClient: (info) => {
    if (clients.size >= MAX_CLIENTS) return false;
    if (!ALLOWED_ORIGINS.length) return true;
    return ALLOWED_ORIGINS.includes(info.origin);
  },
});

wss.on("connection", (ws) => {
  const client = { ws, id: 0, room: 0, game: "", alive: true, count: 0, windowStart: Date.now() };
  clients.add(client);
  ws.on("pong", () => { client.alive = true; });
  ws.on("message", (data, isBinary) => {
    if (!isBinary) return;
    const now = Date.now();
    if (now - client.windowStart > 1000) { client.windowStart = now; client.count = 0; }
    if (++client.count > MAX_MSGS_PER_SEC) { ws.close(1008, "slow down"); return; }
    try { onPacket(client, Buffer.isBuffer(data) ? data : Buffer.concat(data)); } catch (e) { log("packet error", e.message); }
  });
  ws.on("close", () => {
    leaveRoom(client);
    clients.delete(client);
    if (client.id) byId.delete(client.id);
  });
  ws.on("error", () => {});
});

setInterval(() => {
  for (const c of clients) {
    if (!c.alive) { c.ws.terminate(); continue; }
    c.alive = false;
    try { c.ws.ping(); } catch (e) {}
  }
}, 30000).unref();

if (require.main === module) {
  server.listen(PORT, () => log("relay listening on port", PORT, ALLOWED_ORIGINS.length ? "(origins: " + ALLOWED_ORIGINS.join(", ") + ")" : "(any origin)"));
}
module.exports = { server };
