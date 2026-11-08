"use strict";
const assert = require("assert");
const WebSocket = require("ws");
process.env.PORT = "0";
const { server } = require("./relay.js");

const HEADER = 16;
function pkt(header, player, room, type, d0, extra) {
  const b = Buffer.alloc(HEADER + 8 + (extra || 0));
  b[0] = header; b.write("SONIC2", 1, 7, "latin1");
  b.writeUInt32LE(player >>> 0, 8); b.writeUInt32LE(room >>> 0, 12);
  b.writeInt32LE(type | 0, 16); b.writeInt32LE(d0 | 0, 20);
  return b;
}
function client(url) {
  return new Promise((resolve) => {
    const ws = new WebSocket(url);
    const c = { ws, q: [], waiters: [] };
    ws.on("message", (d) => { const m = Buffer.from(d); const w = c.waiters.shift(); if (w) w(m); else c.q.push(m); });
    c.next = () => new Promise((res, rej) => {
      if (c.q.length) return res(c.q.shift());
      const t = setTimeout(() => rej(new Error("timeout waiting for packet")), 2000);
      c.waiters.push((m) => { clearTimeout(t); res(m); });
    });
    ws.on("open", () => resolve(c));
  });
}

server.listen(0, async () => {
  const url = "ws://127.0.0.1:" + server.address().port;
  try {
    const host = await client(url), guest = await client(url), third = await client(url);

    host.ws.send(pkt(0x00, 0, 0x1f2f3f4f, 0, 0));
    guest.ws.send(pkt(0x00, 0, 0x1f2f3f4f, 0, 0));
    third.ws.send(pkt(0x00, 0, 0x1f2f3f4f, 0, 0));
    const hid = (await host.next()).readUInt32LE(8), gid = (await guest.next()).readUInt32LE(8), tid = (await third.next()).readUInt32LE(8);
    assert(hid && gid && tid && hid !== gid && gid !== tid);

    const settings = (4 << 4) | (1 << 8);
    host.ws.send(pkt(0x00, hid, 0, 0x0ff0, settings));
    const hostReply = await host.next();
    const room = hostReply.readUInt32LE(12);
    assert.strictEqual(hostReply[0], 0x00); assert.strictEqual(hostReply.readInt32LE(16), 1);
    assert.strictEqual(room & 0xff0, settings & 0xff0, "settings must ride in the room code");
    console.log("ok: host got room", room.toString(16).padStart(8, "0"));

    third.ws.send(pkt(0x01, tid, 0xdeadbee0, 0, 0));
    assert.strictEqual((await third.next())[0], 0x81);
    console.log("ok: unknown room -> NO_ROOM");

    guest.ws.send(pkt(0x01, gid, room, 0, 0));
    const g = await guest.next();
    assert.strictEqual(g[0], 0x00); assert.strictEqual(g.readInt32LE(16), 2); assert.strictEqual(g.readUInt32LE(20), hid);
    const h = await host.next();
    assert.strictEqual(h[0], 0x01); assert.strictEqual(h.readUInt32LE(8), gid);
    console.log("ok: guest joined, host notified");

    third.ws.send(pkt(0x01, tid, room, 0, 0));
    const full = await third.next();
    assert.strictEqual(full[0], 0x00); assert.strictEqual(full.readInt32LE(16), 3);
    console.log("ok: full room -> type 3");

    const big = pkt(0x10, hid, room, 1, 0, 600); big.write("hello-from-host", 24, "latin1");
    host.ws.send(big);
    const gotG = await guest.next();
    assert.strictEqual(gotG[0], 0x10); assert.strictEqual(gotG.readUInt32LE(8), hid); assert.strictEqual(gotG.length, big.length);
    assert.strictEqual(gotG.toString("latin1", 24, 39), "hello-from-host");
    guest.ws.send(pkt(0x11, gid, room, 0, 1234));
    const gotH = await host.next();
    assert.strictEqual(gotH[0], 0x11); assert.strictEqual(gotH.readInt32LE(20), 1234);
    console.log("ok: data relayed both ways");

    third.ws.send(pkt(0x10, tid, room, 1, 0));
    await new Promise((r) => setTimeout(r, 150));
    assert.strictEqual(host.q.length, 0); assert.strictEqual(guest.q.length, 0);
    console.log("ok: outsiders ignored");

    guest.ws.send(pkt(0xff, gid, room, 0, 0));
    const left = await host.next();
    assert.strictEqual(left[0], 0xff); assert.strictEqual(left.readUInt32LE(8), gid);
    console.log("ok: leave notifies the other player");

    host.ws.send(pkt(0x00, hid, 0, 0x0ff0, settings));
    const r2 = (await host.next()).readUInt32LE(12);
    third.ws.send(pkt(0x01, tid, r2, 0, 0)); await third.next(); await host.next();
    third.ws.close();
    const dropped = await host.next();
    assert.strictEqual(dropped[0], 0xff);
    console.log("ok: dropped connection treated as leave");
    console.log("\nALL RELAY CHECKS PASSED");
    process.exit(0);
  } catch (e) { console.error("FAILED:", e.message); process.exit(1); }
});
