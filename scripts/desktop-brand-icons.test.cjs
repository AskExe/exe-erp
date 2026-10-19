const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../frappe/public/js/frappe/utils/utils.js"), "utf8");
const start = source.indexOf("get_desktop_icon(icon_name, variant) {");
const method = source.slice(start, source.indexOf("\n\tdesktop_icon_exists(", start));
for (const app of ["frappe", "erpnext", "customer_app"]) {
  test(`desktop artwork cache URL for ${app}`, () => {
    const url = `assets/${app}/icons/desktop_icons/solid/projects.svg`;
    const frappe = { scrub: value => value.toLowerCase(), boot: { desktop_icon_urls: { [app]: { solid: [url] } } } };
    const getIcon = new Function("frappe", `return ({${method}}).get_desktop_icon`)(frappe);
    const receiver = { get_desktop_icon_by_label: () => ({ app }) };
    assert.equal(getIcon.call(receiver, "Projects", "Solid"), "/" + url + (app === "customer_app" ? "" : "?v=exe-intelligence-4"));
    assert.equal(getIcon.call(receiver, "Missing", "Solid"), false);
  });
}

test("persisted stock logos refresh while customer images remain byte-identical", () => {
  const start = source.indexOf("desktop_brand_image_url(url) {");
  const method = source.slice(start, source.indexOf("\n\tget_desktop_icon(", start));
  const imageUrl = new Function(`return ({${method}}).desktop_brand_image_url`)();
  for (const stock of ["/assets/frappe/images/frappe-framework-logo.svg", "/assets/erpnext/images/erpnext-logo.svg"]) {
    assert.equal(imageUrl(stock), stock + "?v=exe-intelligence-6");
    assert.equal(imageUrl(stock + "?v=exe-gold-1"), stock + "?v=exe-intelligence-6");
    assert.equal(imageUrl(imageUrl(stock)), imageUrl(stock));
  }
  for (const custom of ["/files/company-logo.svg", "https://customer.test/logo.svg", "/assets/frappe/images/frappe-framework-logo.svg?custom=1", null]) {
    assert.equal(imageUrl(custom), custom);
  }
});

test("bundled legacy desktop images refresh without changing custom URLs", () => {
  const start = source.indexOf("desktop_brand_image_url(url) {");
  const method = source.slice(start, source.indexOf("\n\tget_desktop_icon(", start));
  const imageUrl = new Function(`return ({${method}}).desktop_brand_image_url`)();
  const directory = path.join(__dirname, "../apps/erpnext/erpnext/public/desktop_icons");
  const legacyPaths = fs.readdirSync(directory).filter(name => name.endsWith(".svg"));
  assert.ok(legacyPaths.includes("subcontracting.svg"));
  for (const name of legacyPaths) {
    const url = `/assets/erpnext/desktop_icons/${name}`;
    assert.equal(imageUrl(url), url + "?v=exe-intelligence-4");
    assert.equal(imageUrl(imageUrl(url)), imageUrl(url));
    for (const custom of [url + "?custom=1", `https://customer.test${url}`, url.replace("/erpnext/", "/customer_app/")]) {
      assert.equal(imageUrl(custom), custom);
    }
  }
  for (const custom of ["/assets/erpnext/desktop_icons/customer.svg", "/files/subcontracting.svg", "/assets/erpnext/desktop_icons/../images/erpnext-logo.svg", "assets/erpnext/desktop_icons/subcontracting.svg", undefined]) {
    assert.equal(imageUrl(custom), custom);
  }
});
