/**
 * exe-service-switcher v1.0.0
 * Universal service navigation for Exe products.
 * (c) Exe / Odd Bold — https://askexe.com
 */
;(function(){
"use strict";
/**
 * <exe-service-switcher> — Universal service navigation bar for Exe products.
 *
 * Usage:
 *   <exe-service-switcher current="CRM"></exe-service-switcher>
 *   <exe-service-switcher current="ERP" user="henry@oddbold.com"></exe-service-switcher>
 *
 * Attributes:
 *   current  — "CRM" | "Wiki" | "ERP" (highlights the active service)
 *   user     — optional email to display on the right side
 *   base-url — override base domain (default: derived from window.location.hostname)
 *
 * Design: Exe Foundry Bold — #0F0E1A bg, #F5D76E gold, Manrope 600
 * Ships as a self-contained Web Component with Shadow DOM encapsulation.
 */

const SERVICES = [
  { key: "CRM",  label: "CRM",  path: "crm" },
  { key: "Wiki", label: "Wiki", path: "wiki" },
  { key: "ERP",  label: "ERP",  path: "erp"  },
];

const EXE_BRAND_MARK = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 520 520" aria-hidden="true" focusable="false"><rect width="520" height="520" rx="88" fill="#F5D76E"/><g transform="translate(0 105)"><g transform="translate(60 186) scale(1.4547570555717193)"><g transform="translate(0 -14)"><path fill="#000000" d="M20.607830810546876 -86.45025634765626 30.4076513671875 -96.81011108398438V-54.01195556640626L25.4517333984375 -60.19987182617188H63.559794921875V-43.40012817382813H25.4517333984375L30.4076513671875 -49.54604614257813V-6.439888916015626L20.607830810546876 -16.79974365234375H71.329794921875V0.0H8.568052978515626V-103.25000000000001H71.329794921875V-86.45025634765626Z" /><path fill="#000000" d="M128.61801025390628 -59.83584106445313 128.4220153808594 -42.25216064453125 95.01803588867189 -103.25000000000001H119.39158813476564L141.72179321289065 -56.741904296875006H133.16781372070312L155.28801879882815 -103.25000000000001H179.70356933593752L146.57958984375 -42.25216064453125L146.42559326171875 -59.83584106445313L180.7115710449219 0.0H156.26801879882814L132.78982055664062 -45.37409912109376H142.05778808593752L118.55158813476564 0.0H94.17803588867189Z" /><path fill="#000000" d="M215.69783081054692 -86.45025634765626 225.49765136718753 -96.81011108398438V-54.01195556640626L220.54173339843754 -60.19987182617188H258.649794921875V-43.40012817382813H220.54173339843754L225.49765136718753 -49.54604614257813V-6.439888916015626L215.69783081054692 -16.79974365234375H266.41979492187505V0.0H203.65805297851566V-103.25000000000001H266.41979492187505V-86.45025634765626Z" /></g><g transform="translate(0 14)"><path fill="#000000" d="M1.989366527785488 52.0V27.987803433984595H6.970788436320496V52.0Z" /><path fill="#000000" d="M20.749715824359836 52.0H15.830154653618086V27.987803433984595H23.663694603639367L31.715600235554522 50.17996603886823L30.869094394103403 50.38833988711423V27.987803433984595H35.78865556484516V52.0H27.88023051789614L19.84134924511546 29.869698132909626L20.749715824359836 29.66783646423086Z" /><path fill="#000000" d="M60.588823871257986 31.894813088597164H52.657513789247545L54.68913297554954 29.485487487395453V52.0H49.64585032922127V29.485487487395453L51.736079131628394 31.894813088597164H43.762438888811815V27.987803433984595H60.588823871257986Z" /><path fill="#000000" d="M71.35932501029181 31.894813088597164 73.63840702014879 29.485487487395453V39.438782624935236L72.48584066246673 37.99969825143853H81.34839043535379V41.906707906051096H72.48584066246673L73.63840702014879 40.477390808286145V50.50231594658914L71.35932501029181 48.092990345387435H83.15540997354208V52.0H68.55931036705802V27.987803433984595H83.15540997354208V31.894813088597164Z" /><path fill="#000000" d="M92.01800545290764 52.0V27.987803433984595H97.07756755453491V50.49254867085739L94.79848554467793 47.88787755502054H106.00524309757073V52.0Z" /><path fill="#000000" d="M114.07666738304383 52.0V27.987803433984595H119.13622948467109V50.49254867085739L116.85714747481411 47.88787755502054H128.0639050277069V52.0Z" /><path fill="#000000" d="M136.13207819149187 52.0V27.987803433984595H141.1135001000269V52.0Z" /><path fill="#000000" d="M159.5940005521713 52.38419276037041Q156.7418467403975 52.38419276037041 154.40737841478878 50.9629945242539Q152.07291008918006 49.5417962881374 150.6972990972724 46.79545198047337Q149.32168810536476 44.04910767280935 149.32168810536476 40.05739000286782Q149.32168810536476 36.06567233292628 150.76729856909606 33.29490983593287Q152.21290903282738 30.52414733893945 154.66295910637314 29.075274823709933Q157.11300917991886 27.626402308480415 160.13772283317616 27.626402308480415Q162.66102846832916 27.626402308480415 164.4143190474209 28.440374079810994Q166.16760962651261 29.25434585114157 167.26646888471828 30.52902600870984Q168.36532814292397 31.803706166278115 168.86510563208867 33.2102505079397Q169.3648831212534 34.61679484960128 169.3648831212534 35.80845216983052Q169.3648831212534 35.85729053572749 169.3648831212534 35.90612890162447Q169.3648831212534 35.954967267521454 169.3648831212534 36.00380563341844H164.45508922624342Q164.45508922624342 35.954967267521454 164.45508922624342 35.90612890162447Q164.45508922624342 35.85729053572749 164.45508922624342 35.80845216983052Q164.45508922624342 35.046564917989556 164.1799640470046 34.31072340356025Q163.90483886776576 33.57488188913095 163.35621506375128 32.983928917929425Q162.8075912597368 32.3929759467279 162.0001197445443 32.04133414800269Q161.19264822935176 31.689692349277486 160.13772283317616 31.689692349277486Q158.70510890724455 31.689692349277486 157.4027395662768 32.500419954253616Q156.10037022530906 33.31114755922975 155.27336813312942 35.14260019103389Q154.44636604094978 36.974052822838026 154.44636604094978 40.05739000286782Q154.44636604094978 42.39512733530842 154.9103334978284 43.988896342718505Q155.374300954707 45.582665350128586 156.15734931247465 46.5382795110943Q156.94039767024228 47.49389367206002 157.9383390111568 47.918794013249794Q158.93628035207138 48.34369435443957 160.01074029971053 48.34369435443957Q161.48893352089817 48.34369435443957 162.49012995797725 47.84716401248659Q163.49132639505632 47.3506336705336 164.06273984669883 46.613163614402936Q164.63415329834137 45.875693558272275 164.79043765900227 45.162642685090105V43.60628741119005H160.03679100521765V39.94997580391503H169.28023074859394V52.0H166.61693610598456L166.1578681848774 49.339936606696824Q166.0373998182172 49.65249936630403 165.61576252214473 50.160417576737345Q165.1941252260723 50.66833578717066 164.4192245449069 51.179511081006694Q163.64432386374156 51.690686374842734 162.449413434586 52.03743956760657Q161.25450300543045 52.38419276037041 159.5940005521713 52.38419276037041Z" /><path fill="#000000" d="M181.034015359611 31.894813088597164 183.31309736946798 29.485487487395453V39.438782624935236L182.1605310117859 37.99969825143853H191.02308078467297V41.906707906051096H182.1605310117859L183.31309736946798 40.477390808286145V50.50231594658914L181.034015359611 48.092990345387435H192.83010032286126V52.0H178.2340007163772V27.987803433984595H192.83010032286126V31.894813088597164Z" /><path fill="#000000" d="M206.61225697296857 52.0H201.69269580222684V27.987803433984595H209.5262357522481L217.57814138416325 50.17996603886823L216.73163554271213 50.38833988711423V27.987803433984595H221.65119671345388V52.0H213.7427716665049L205.7038903937242 29.869698132909626L206.61225697296857 29.66783646423086Z" /><path fill="#000000" d="M250.16632002629618 43.21887929941903Q250.16632002629618 44.78495812981053 249.57537798490472 46.39499268185224Q248.9844359435133 48.00502723389394 247.78464485734912 49.36273122242035Q246.58485377118498 50.720435210946754 244.7501669092174 51.552313985658586Q242.9154800472498 52.38419276037041 240.4182231303657 52.38419276037041Q237.0809467175012 52.38419276037041 234.71880709363677 50.796945868718545Q232.35666746977233 49.20969897706668 231.11455019727845 46.416144845207036Q229.87243292478456 43.62259071334739 229.87243292478456 39.992274168910036Q229.87243292478456 36.35544544490545 231.11455019727845 33.5700310406953Q232.35666746977233 30.78461663648515 234.71880709363677 29.20550947248278Q237.0809467175012 27.626402308480415 240.4182231303657 27.626402308480415Q243.19223277457087 27.626402308480415 245.15390018276588 28.638985620420726Q247.11556759096092 29.651568932361037 248.29582313204247 31.263235006961395Q249.476078673124 32.87490108156175 249.89934053642145 34.66238209381012Q250.16632002629618 35.75960784221894 250.16632002629618 36.768924134565566H245.09699064893715Q245.09699064893715 36.28054246283396 245.02536064817394 35.81820156041851Q244.8593090117813 34.63630555420644 244.26998656951935 33.70185548002469Q243.68066412725736 32.76740540584295 242.71202560715716 32.225292787776596Q241.743387087057 31.683180169710248 240.4182231303657 31.683180169710248Q238.84886535695134 31.683180169710248 237.63115037588125 32.57530306394315Q236.4134353948112 33.46742595817606 235.71178530715764 35.3070183176297Q235.01013521950406 37.14661067708333 235.01013521950406 39.992274168910036Q235.01013521950406 42.828170385004995 235.71178530715764 44.68078610997401Q236.4134353948112 46.533401834943014 237.63115037588125 47.43854809469129Q238.84886535695134 48.34369435443957 240.4182231303657 48.34369435443957Q241.54477455282816 48.34369435443957 242.41247925773166 47.93181936600337Q243.28018396263516 47.51994437756716 243.87601858446436 46.80364138907776Q244.4718532062936 46.087338400588365 244.78442192761537 45.16428613107793Q245.09699064893715 44.241233861567494 245.09699064893715 43.21887929941903Z" /><path fill="#000000" d="M261.177773796556 31.894813088597164 263.45685580641293 29.485487487395453V39.438782624935236L262.3042894487309 37.99969825143853H271.166839221618V41.906707906051096H262.3042894487309L263.45685580641293 40.477390808286145V50.50231594658914L261.177773796556 48.092990345387435H272.97385875980626V52.0H258.3777591533222V27.987803433984595H272.97385875980626V31.894813088597164Z" /></g></g></g></svg>`;

// Sign-out keeps its existing icon; product links are text only.
const ICONS = {
  logout: `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M5 12H3a1 1 0 01-1-1V3a1 1 0 011-1h2M9 10l3-3-3-3M12 7H5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`,
};

class ExeServiceSwitcher extends HTMLElement {
  static get observedAttributes() {
    return ["current", "user", "base-url"];
  }

  constructor() {
    super();
    this._shadow = this.attachShadow({ mode: "open" });
    for (const type of ["pointerover", "focusin"]) {
      this._shadow.addEventListener?.(type, (event) => {
        const link = event.target.closest?.(".exe-ss-link:not(.exe-ss-active)");
        if (link) this._warmOrigin(link.href);
      });
    }
  }

  connectedCallback() {
    this._render();
    this._scheduleWarmth();
  }

  attributeChangedCallback() {
    if (this._shadow) this._render();
  }

  get _current() {
    return (this.getAttribute("current") || "").toUpperCase();
  }

  get _user() {
    return this.getAttribute("user") || "";
  }

  get _baseDomain() {
    // Explicit override always wins.
    const override = this.getAttribute("base-url");
    if (override) return override;
    // Otherwise derive the shared base domain from the current host so
    // white-label installs link to the customer's own services
    // (erp.acme.com -> acme.com -> crm.acme.com / wiki.acme.com). Never
    // hardcode askexe.com, which would point customers back at AskExe.
    try {
      const host = (typeof window !== "undefined" && window.location && window.location.hostname) || "";
      if (host) {
        const labels = host.split(".");
        // Strip the leading service label (e.g. "erp") for multi-label hosts.
        // Single-label hosts (e.g. "localhost") are used as-is.
        return labels.length >= 2 ? labels.slice(1).join(".") : host;
      }
    } catch (_e) {
      // window/location unavailable — fall through to neutral default.
    }
    return "localhost";
  }

  _serviceUrl(path) {
    const proto = this._baseDomain.includes("localhost") ? "http" : "https";
    return `${proto}://${path}.${this._baseDomain}`;
  }

  _render() {
    const current = this._current;
    const user = this._user;
    const domain = this._baseDomain;

    const serviceLinks = SERVICES.map((s) => {
      const isCurrent = s.key.toUpperCase() === current;
      const url = this._serviceUrl(s.path);
      return `
        <a
          href="${url}"
          class="exe-ss-link${isCurrent ? " exe-ss-active" : ""}"
          ${isCurrent ? 'aria-current="page"' : ""}
          title="${s.label}"
        >
          <span class="exe-ss-label">${s.label}</span>
        </a>
      `;
    }).join('<span class="exe-ss-sep" aria-hidden="true"></span>');

    const userSection = user
      ? `
        <div class="exe-ss-user">
          <span class="exe-ss-email">${this._escapeHtml(user)}</span>
          <button type="button" class="exe-ss-logout" title="Sign out" aria-label="Sign out">
            ${ICONS.logout}
          </button>
          <span class="exe-ss-logout-status" role="status"></span>
        </div>
      `
      : "";

    this._shadow.innerHTML = `
      <style>${this._styles()}</style>
      <nav class="exe-ss-bar" aria-label="Exe service navigation">
        <div class="exe-ss-left">
          <span class="exe-ss-logo" role="img" aria-label="EXE Intelligence">${EXE_BRAND_MARK}</span>
          ${serviceLinks}
        </div>
        <div class="exe-ss-right">
          ${userSection}
        </div>
      </nav>
    `;
    this._shadow.querySelector(".exe-ss-logout")?.addEventListener("click", () => this._logout());
  }

  async _logout() {
    if (this._signingOut) return;
    const button = this._shadow.querySelector(".exe-ss-logout");
    const status = this._shadow.querySelector(".exe-ss-logout-status");
    if (!window.frappe?.call) {
      status.textContent = "ERP is still loading. Try signing out again shortly.";
      return;
    }
    this._signingOut = true;
    button.disabled = true;
    status.textContent = "Signing out…";
    try {
      // ERP's logout RPC is behind the shared auth gate. End its native
      // session while that gate still admits the request, then revoke SSO.
      // A retry after a central outage must not repeat the native logout RPC.
      await new Promise((resolve, reject) => {
        window.frappe.call({
          method: "logout",
          callback: (response) => response.exc ? reject(new Error("logout_failed")) : resolve(),
          error: reject,
        });
      });
      // The central Auth landing page POSTs its same-origin GoTrue logout and
      // clears HttpOnly browser cookies. The product API can deny an expired
      // or unlicensed account with 403; that must never prevent signing out.
      window.location.assign(`https://auth.${this._baseDomain}/logout`);
    } catch (_error) {
      this._signingOut = false;
      button.disabled = false;
      status.textContent = "Could not sign out. Please try again.";
    }
  }

  _scheduleWarmth() {
    if (!this.isConnected || this._warmTimer) return;
    // One low-priority connection after the host settles; never load app HTML.
    this._warmTimer = window.setTimeout(() => {
      this._warmTimer = null;
      if (!this.isConnected || document.visibilityState === "hidden") return;
      const link = this._shadow.querySelector?.(".exe-ss-link:not(.exe-ss-active)");
      if (link) this._warmOrigin(link.href);
    }, 2000);
  }

  disconnectedCallback() {
    window.clearTimeout(this._warmTimer);
    this._warmTimer = null;
  }

  _warmOrigin(href) {
    const connection = window.navigator?.connection;
    if (connection?.saveData || /(^|-)2g$/.test(connection?.effectiveType || "")) return;
    let url;
    try { url = new URL(href); } catch { return; }
    // Hints never follow callbacks or fetch a document, token, or private data.
    if (url.protocol !== "https:" || url.username || url.password ||
        url.pathname !== "/" || url.search || url.hash ||
        url.origin === window.location.origin ||
        !SERVICES.some((service) => this._serviceUrl(service.path) === url.origin)) return;
    const origin = this._staticOrigin(url.origin);
    const warmed = ExeServiceSwitcher._warmedOrigins ||= new Set();
    if (warmed.has(origin) || warmed.size >= 2) return;
    warmed.add(origin);
    const hint = document.createElement("link");
    hint.rel = "preconnect";
    hint.href = origin;
    hint.crossOrigin = "anonymous";
    document.head.appendChild(hint);
    void this._warmWikiAssets(url.origin);
  }

  _staticOrigin(origin) {
    // Demo context is only a public-static routing hint, never authorization.
    const demo = this.getAttribute("static-context") === "demo" ||
      /^demo-(crm|wiki|erp|dashboard)\.askexe\.com$/.test(window.location.hostname);
    // Only the known AskExe demo deployment has a declared alternate origin.
    // Custom/configured domains retain their exact normal Wiki destination.
    return demo && origin === "https://wiki.askexe.com"
      ? "https://demo-wiki.askexe.com" : origin;
  }

  async _warmWikiAssets(origin) {
    if (origin !== this._serviceUrl("wiki") || ExeServiceSwitcher._staticWarmStarted) return;
    origin = this._staticOrigin(origin);
    ExeServiceSwitcher._staticWarmStarted = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 5000);
    const options = { credentials: "omit", mode: "cors", redirect: "error", signal: controller.signal };
    try {
      const response = await fetch(`${origin}/exe-boot-assets.json`, { ...options, cache: "no-cache" });
      if (!response.ok || !response.headers.get("content-type")?.includes("application/json")) return;
      const text = await response.text();
      if (text.length > 4096) return;
      const manifest = JSON.parse(text);
      if (manifest.version !== 1 || !Array.isArray(manifest.assets) || manifest.assets.length > 4) return;
      let bytes = 0;
      const paths = new Set();
      for (const asset of manifest.assets) {
        if (!/^\/assets\/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8,}\.(js|css)$/.test(asset.path) ||
            !Number.isSafeInteger(asset.bytes) || asset.bytes <= 0 || paths.has(asset.path)) return;
        paths.add(asset.path);
        bytes += asset.bytes;
      }
      if (bytes > 2 * 1024 * 1024) return;
      for (const path of paths) {
        const asset = await fetch(`${origin}${path}`, { ...options, cache: "force-cache" });
        if (!asset.ok) return;
        // Finish the bounded public download without evaluating JavaScript.
        await asset.arrayBuffer();
      }
    } catch { /* Missing index, CORS, offline, or timeout: ordinary navigation continues. */ }
    finally { window.clearTimeout(timer); }
  }

  _escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _styles() {
    return `
      :host {
        display: block;
        /* Stretch to full viewport width in any mount context. Frappe's desk
           sets body { display: flex }, which otherwise shrinks this element to
           content width (~389px) and leaves a ragged edge where the dark bar
           meets the page. width:100% + flex:1 1 auto cover both block and
           flex-item parents. */
        width: 100%;
        flex: 1 1 auto;
        font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        font-size: 12px;
        font-weight: 600;
        line-height: 1;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
      }

      *,
      *::before,
      *::after {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
      }

      .exe-ss-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        height: 32px;
        padding: 0 16px;
        background: #131222;
        border-bottom: 1px solid rgba(160, 156, 175, 0.1);
        user-select: none;
      }

      .exe-ss-left,
      .exe-ss-right {
        display: flex;
        align-items: center;
        gap: 0;
      }

      .exe-ss-logo {
        display: flex;
        align-items: center;
        margin-right: 14px;
        opacity: 1;
      }

      /* --- Service links --- */

      .exe-ss-link {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 4px 10px;
        border-radius: 4px;
        color: #A09CAF;
        text-decoration: none;
        letter-spacing: 0.02em;
        transition: color 0.15s ease, background 0.15s ease;
        white-space: nowrap;
      }

      .exe-ss-link:hover {
        color: #F0EDE8;
        background: rgba(240, 237, 232, 0.06);
      }

      .exe-ss-link.exe-ss-active {
        color: #F5D76E;
      }

      .exe-ss-link.exe-ss-active:hover {
        color: #F5D76E;
        background: rgba(245, 215, 110, 0.08);
      }

      .exe-ss-logo svg {
        width: 24px;
        height: 24px;
      }

      .exe-ss-label {
        font-size: 12px;
        font-weight: 600;
      }

      /* --- Separator dot --- */

      .exe-ss-sep {
        display: inline-block;
        width: 3px;
        height: 3px;
        border-radius: 50%;
        background: rgba(160, 156, 175, 0.3);
        margin: 0 2px;
        vertical-align: middle;
      }

      /* --- User section --- */

      .exe-ss-user {
        display: flex;
        align-items: center;
        gap: 10px;
      }

      .exe-ss-email {
        color: #A09CAF;
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.01em;
      }

      .exe-ss-logout {
        border: 0;
        background: transparent;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        padding: 3px;
        border-radius: 3px;
        color: #A09CAF;
        text-decoration: none;
        transition: color 0.15s ease, background 0.15s ease;
      }

      .exe-ss-logout:hover {
        color: #F0EDE8;
        background: rgba(240, 237, 232, 0.08);
      }

      /* --- Responsive --- */

      @media (max-width: 480px) {
        .exe-ss-bar {
          padding: 0 10px;
        }

        .exe-ss-email {
          display: none;
        }

        .exe-ss-link {
          padding: 4px 6px;
          gap: 3px;
        }

        .exe-ss-label {
          font-size: 11px;
        }
      }
    `;
  }
}

// Register only once (safe for multiple script loads)
if (!customElements.get("exe-service-switcher")) {
  customElements.define("exe-service-switcher", ExeServiceSwitcher);
}

})();
