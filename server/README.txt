Multiplayer relay for the Sonic web port (2-player VS, room codes)
------------------------------------------------------------------
Run on your own computer (WSL/Ubuntu):

  cd server
  npm install          (first time only)
  node selftest.js     (optional: should end with ALL RELAY CHECKS PASSED)
  node relay.js        (leave this running; it prints "room opened" etc.)

The web page connects to the address in web/shell.html:
  window.RSDK_RELAY_URL = "ws://localhost:8787";

For a public site the relay must be hosted somewhere and the address must start
with wss:// . When you host it, set ALLOWED_ORIGINS to your site, e.g.
  ALLOWED_ORIGINS=https://yourname.github.io node relay.js
