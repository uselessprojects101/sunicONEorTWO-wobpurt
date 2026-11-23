import pathlib
import re
import sys

BADGE = (
    '<div id="testBadge" style="position:fixed;bottom:8px;right:8px;z-index:9;pointer-events:none;padding:4px 8px;'
    'background:#f8d800;color:#000;font:700 12px system-ui,sans-serif;border-radius:4px">test build</div>'
)


def need(text, token, name):
    if token not in text:
        sys.exit("channel.py: could not find %s in the build output (%s)" % (name, token))


def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    page = out / "index.html"
    net = out / "net.js"
    html = page.read_text(encoding="utf-8")
    if "rsdkweb_test_" in html:
        print("  test channel already applied")
        return

    need(html, "/rsdkweb_s", "the game storage folders")
    need(html, "rsdkweb_stored", "the stored-games note")
    need(html, "rsdkweb_settings", "the settings note")
    html = html.replace("/rsdkweb_s", "/rsdkweb_test_s")
    html = html.replace("rsdkweb_stored", "rsdkweb_test_stored")
    html = html.replace("rsdkweb_settings", "rsdkweb_test_settings")
    html, count = re.subn(r"<title>(.*?)</title>", lambda m: "<title>[TEST] " + m.group(1) + "</title>", html, count=1, flags=re.S)
    if not count:
        sys.exit("channel.py: could not find the page title")
    anchor = re.search(r"<div\b[^>]*\bid\s*=\s*[\"']setup[\"'][^>]*>", html) or re.search(r"<body\b[^>]*>", html)
    if not anchor:
        sys.exit("channel.py: could not find where to put the test badge")
    html = html[:anchor.end()] + BADGE + html[anchor.end():]
    page.write_text(html, encoding="utf-8")

    js = net.read_text(encoding="utf-8")
    need(js, '"rsdkweb-"', "the room prefix")
    net.write_text(js.replace('"rsdkweb-"', '"rsdktest-"'), encoding="utf-8")
    print("  test channel applied: separate storage names, separate room prefix, test badge")


if __name__ == "__main__":
    main()
