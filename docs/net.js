// Multiplayer transport for the Sonic web port.
//
// The game's C++ code (NetworkingWeb.cpp) talks to window.RSDKNet. It sends and receives the
// same packets the original 2-player VS server used. Two ways of carrying them:
//
//   "p2p"   (default) players connect straight to each other with WebRTC. A small public
//           service (PeerJS cloud) only introduces them. Nothing to host.
//           This file plays the part of the old server for the two players.
//   "relay" players connect to a relay server over a WebSocket (see /server).
//
// Settings (optional, set in the page before the game starts):
//   window.RSDK_NET_MODE      = "p2p" | "relay"
//   window.RSDK_RELAY_URL     = "wss://your-relay.example.com"      (relay mode)
//   window.RSDK_PEER_OPTIONS  = { host: "...", port: 9000, ... }     (p2p mode, own PeerJS server)
(function () {
  "use strict";

  var HEADER = 16;
  var CL = { REQUEST_CODE: 0x00, JOIN: 0x01, DATA: 0x10, DATA_VERIFIED: 0x11, LEAVE: 0xff };
  var SV = { CODES: 0x00, NEW_PLAYER: 0x01, DATA: 0x10, DATA_VERIFIED: 0x11, NO_ROOM: 0x81, LEAVE: 0xff };
  var HELLO_ROOM = 0x1f2f3f4f;
  var PEER_PREFIX = "rsdkweb-";
  var FRAME_HELLO = 0xfe; // sent once by each side after connecting: carries the sender's player id
  var FRAME_FULL = 0xfd;  // host -> a third player: room is full
  var CONNECT_TIMEOUT_MS = 15000;

  function cfg(name, dflt) { var v = window[name]; return v === undefined ? dflt : v; }
  function log(msg) { if (window.diagLog) window.diagLog(msg); else console.warn(msg); }
  function rand32() {
    var a = new Uint32Array(1);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return a[0];
  }
  function randomId() { var v = 0; while (!v) v = rand32(); return v >>> 0; }
  function hex8(n) { var s = (n >>> 0).toString(16); while (s.length < 8) s = "0" + s; return s; }
  function latin1(buf, from, to) {
    var s = "";
    for (var i = from; i < to && buf[i]; i++) s += String.fromCharCode(buf[i]);
    return s;
  }

  // packets the game expects from the server
  function makeCodes(game, player, room, type, data0) {
    var b = new Uint8Array(HEADER + 8), dv = new DataView(b.buffer);
    b[0] = SV.CODES;
    for (var i = 0; i < 7 && i < game.length; i++) b[1 + i] = game.charCodeAt(i);
    dv.setUint32(8, player >>> 0, true);
    dv.setUint32(12, room >>> 0, true);
    dv.setInt32(16, type | 0, true);
    dv.setInt32(20, data0 | 0, true);
    return b;
  }
  function makeSimple(header, game, player, room) {
    var b = makeCodes(game, player, room, 0, 0);
    b[0] = header;
    return b;
  }

  // =====================================================================================
  //  Direct player-to-player mode
  // =====================================================================================
  function P2P() {
    this.rx = [];
    this.st = 2;      // 1 = connecting, 2 = ready, 3 = closed/failed
    this.id = 0;      // our player id
    this.game = "";
    this.room = 0;
    this.role = null; // "host" | "guest"
    this.peer = null;
    this.conn = null;
    this.partnerId = 0;
    this.timer = 0;
  }
  P2P.prototype.PeerClass = function () {
    var p = window.Peer || (window.peerjs && window.peerjs.Peer);
    if (!p) { log("multiplayer: the PeerJS library is missing from the page"); }
    return p;
  };
  P2P.prototype.peerOptions = function () {
    var o = cfg("RSDK_PEER_OPTIONS", null);
    if (o) return o;
    return {
      debug: 0,
      config: { iceServers: [{ urls: "stun:stun.l.google.com:19302" }, { urls: "stun:global.stun.twilio.com:3478" }] },
    };
  };
  P2P.prototype.push = function (buf) { if (this.rx.length < 512) this.rx.push(buf); };

  // from the game
  P2P.prototype.send = function (buf) {
    if (buf.length < HEADER) return;
    var dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    var header = buf[0], player = dv.getUint32(8, true), room = dv.getUint32(12, true);
    this.game = latin1(buf, 1, 8);

    if (header === CL.REQUEST_CODE && room === HELLO_ROOM) { // hello: hand out a player id
      if (!this.id) this.id = randomId();
      this.push(makeCodes(this.game, this.id, 0, 0, 0));
      return;
    }
    if (!this.id || player !== this.id) return;

    switch (header) {
      case CL.REQUEST_CODE: this.host(buf.length >= HEADER + 8 ? dv.getInt32(HEADER + 4, true) : 0, 0); break;
      case CL.JOIN: this.join(room); break;
      case CL.DATA:
      case CL.DATA_VERIFIED: this.forward(buf, header); break;
      case CL.LEAVE: this.teardown(); break;
    }
  };

  // ---- hosting ----
  P2P.prototype.host = function (settings, attempt) {
    var self = this, Peer = this.PeerClass();
    this.teardown();
    if (!Peer) { this.st = 3; return; }
    // The game reads the match settings out of bits 4-11 of the room code (mask 0xFF0).
    var code = (((rand32() & 0xfffff) << 12) | (settings & 0xff0)) >>> 0;
    if (!code) code = 0x1000;
    var peer = new Peer(PEER_PREFIX + hex8(code), this.peerOptions());
    this.peer = peer; this.role = "host"; this.room = code;

    peer.on("open", function () {
      if (self.peer !== peer) return;
      self.push(makeCodes(self.game, self.id, code, 1, 0));
    });
    peer.on("connection", function (conn) { if (self.peer === peer) self.onIncoming(conn); });
    peer.on("disconnected", function () { // lost the introduction service; existing connections keep working
      if (self.peer === peer && !peer.destroyed && !self.conn) { try { peer.reconnect(); } catch (e) {} }
    });
    peer.on("error", function (e) {
      if (self.peer !== peer) return;
      if (e && e.type === "unavailable-id" && attempt < 5) { self.host(settings, attempt + 1); return; }
      log("multiplayer: could not open a room (" + ((e && e.type) || e) + "). The matchmaking service may be unreachable.");
      self.fail();
    });
  };
  P2P.prototype.onIncoming = function (conn) {
    var self = this;
    if (this.conn) { // already have a guest
      conn.on("open", function () { try { conn.send(new Uint8Array([FRAME_FULL])); } catch (e) {} setTimeout(function () { try { conn.close(); } catch (e) {} }, 400); });
      return;
    }
    this.conn = conn;
    this.wire(conn);
    // the guest speaks first (their hello carries their player id); see onFrame
  };

  // ---- joining ----
  P2P.prototype.join = function (code) {
    var self = this, Peer = this.PeerClass();
    this.teardown();
    if (!Peer) { this.noRoom(code); return; }
    this.role = "guest"; this.room = code;
    var peer = new Peer(undefined, this.peerOptions());
    this.peer = peer;
    this.timer = setTimeout(function () {
      if (self.peer === peer && !self.partnerId) { log("multiplayer: no answer from the host (timed out)"); self.noRoom(code); }
    }, CONNECT_TIMEOUT_MS);

    peer.on("open", function () {
      if (self.peer !== peer) return;
      var conn = peer.connect(PEER_PREFIX + hex8(code), { reliable: true, serialization: "raw" });
      self.conn = conn;
      self.wire(conn);
      conn.on("open", function () { self.sendHello(conn); });
    });
    peer.on("error", function (e) {
      if (self.peer !== peer) return;
      if (!(e && e.type === "peer-unavailable")) log("multiplayer: " + ((e && e.type) || e));
      self.noRoom(code);
    });
  };
  P2P.prototype.noRoom = function (code) {
    this.teardown();
    this.push(makeSimple(SV.NO_ROOM, this.game, this.id, code));
  };

  // ---- both roles ----
  P2P.prototype.sendHello = function (conn) {
    var b = new Uint8Array(5); b[0] = FRAME_HELLO;
    new DataView(b.buffer).setUint32(1, this.id, true);
    conn.send(b);
  };
  P2P.prototype.wire = function (conn) {
    var self = this;
    conn.on("data", function (d) { if (self.conn === conn) self.onFrame(conn, d); });
    conn.on("close", function () { if (self.conn === conn) self.onClosed(); });
    conn.on("error", function (e) { if (self.conn === conn) { log("multiplayer: connection problem (" + ((e && e.type) || e) + ")"); self.onClosed(); } });
  };
  P2P.prototype.onFrame = function (conn, d) {
    var u8 = d instanceof ArrayBuffer ? new Uint8Array(d) : (d && d.buffer ? new Uint8Array(d.buffer, d.byteOffset, d.byteLength) : null);
    if (!u8 || !u8.length) return;
    if (u8[0] === FRAME_HELLO && u8.length >= 5) {
      if (this.partnerId) return;
      this.partnerId = new DataView(u8.buffer, u8.byteOffset, u8.byteLength).getUint32(1, true);
      clearTimeout(this.timer);
      if (this.role === "host") {
        this.sendHello(conn); // answer so the guest learns our id
        this.push(makeSimple(SV.NEW_PLAYER, this.game, this.partnerId, this.room)); // "a player joined"
      } else {
        this.push(makeCodes(this.game, this.id, this.room, 2, this.partnerId)); // "you joined; the host is ..."
      }
      return;
    }
    if (u8[0] === FRAME_FULL) { this.push(makeCodes(this.game, this.id, this.room, 3, 0)); this.teardown(); return; }
    if (!this.partnerId) return;
    if (u8.length >= HEADER && (u8[0] === SV.DATA || u8[0] === SV.DATA_VERIFIED)) this.push(u8);
  };
  P2P.prototype.onClosed = function () {
    var partner = this.partnerId, room = this.room;
    this.conn = null; this.partnerId = 0;
    if (partner) this.push(makeSimple(SV.LEAVE, this.game, partner, room)); // "the other player left"
  };
  P2P.prototype.forward = function (buf, header) {
    if (!this.conn || !this.partnerId || !this.conn.open) return;
    var out = new Uint8Array(buf);
    var dv = new DataView(out.buffer);
    out[0] = header === CL.DATA ? SV.DATA : SV.DATA_VERIFIED;
    dv.setUint32(8, this.id, true);
    dv.setUint32(12, this.room, true);
    try { this.conn.send(out); } catch (e) {}
  };
  P2P.prototype.teardown = function () {
    clearTimeout(this.timer);
    var conn = this.conn, peer = this.peer;
    this.conn = null; this.peer = null; this.partnerId = 0; this.role = null;
    try { if (conn) conn.close(); } catch (e) {}
    try { if (peer) peer.destroy(); } catch (e) {}
  };
  P2P.prototype.fail = function () { this.teardown(); this.st = 3; };
  P2P.prototype.close = function () { this.teardown(); this.st = 3; };

  // =====================================================================================
  //  Relay-server mode (WebSocket)
  // =====================================================================================
  function WSRelay() {
    this.rx = []; this.tx = []; this.st = 1; this.ws = null;
    var url = cfg("RSDK_RELAY_URL", "ws://localhost:8787"), self = this;
    try {
      var ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      this.ws = ws;
      ws.onopen = function () { self.st = 2; for (var i = 0; i < self.tx.length; i++) ws.send(self.tx[i]); self.tx = []; };
      ws.onmessage = function (e) { if (self.rx.length < 512) self.rx.push(new Uint8Array(e.data)); };
      ws.onerror = function () { log("multiplayer: could not reach the relay server (" + url + ")"); };
      ws.onclose = function () { self.st = 3; };
    } catch (e) { this.st = 3; log("multiplayer: " + e.message); }
  }
  WSRelay.prototype.send = function (buf) {
    if (this.st === 3) return;
    if (this.st === 2) this.ws.send(buf); else this.tx.push(buf);
  };
  WSRelay.prototype.close = function () { try { if (this.ws) this.ws.close(); } catch (e) {} this.st = 3; };

  // =====================================================================================
  //  What the game calls
  // =====================================================================================
  var impl = null;
  window.RSDKNet = {
    open: function () {
      if (impl) impl.close();
      impl = cfg("RSDK_NET_MODE", "p2p") === "relay" ? new WSRelay() : new P2P();
    },
    send: function (u8) { if (impl) impl.send(u8); },
    poll: function () { return impl && impl.rx.length ? impl.rx.shift() : null; },
    state: function () { return impl ? impl.st : 0; },
    close: function () { if (impl) impl.close(); },
  };
})();
