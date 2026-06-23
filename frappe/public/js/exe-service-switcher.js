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
  { key: "CRM",  label: "CRM",  path: "crm",  icon: "grid" },
  { key: "Wiki", label: "Wiki", path: "wiki", icon: "book" },
  { key: "ERP",  label: "ERP",  path: "erp",  icon: "box"  },
];

// Inline SVG icons — small, crisp, 14x14
const ICONS = {
  grid: `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="1" y="1" width="5" height="5" rx="1" fill="currentColor"/>
    <rect x="8" y="1" width="5" height="5" rx="1" fill="currentColor"/>
    <rect x="1" y="8" width="5" height="5" rx="1" fill="currentColor"/>
    <rect x="8" y="8" width="5" height="5" rx="1" fill="currentColor"/>
  </svg>`,
  book: `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M2 2.5A1.5 1.5 0 013.5 1H5a1 1 0 011 1v9.5a.5.5 0 01-.854.354A2.5 2.5 0 003.5 11H2.5a.5.5 0 01-.5-.5V2.5z" fill="currentColor"/>
    <path d="M8 2a1 1 0 011-1h1.5A1.5 1.5 0 0112 2.5v8a.5.5 0 01-.5.5h-1a2.5 2.5 0 00-1.646.618A.5.5 0 018 11.5V2z" fill="currentColor"/>
  </svg>`,
  box: `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M7 1L12.5 3.5V10.5L7 13L1.5 10.5V3.5L7 1Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
    <path d="M7 6.5L12.5 3.5M7 6.5L1.5 3.5M7 6.5V13" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
  </svg>`,
  logout: `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M5 12H3a1 1 0 01-1-1V3a1 1 0 011-1h2M9 10l3-3-3-3M12 7H5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`,
  exe: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="8" cy="8" r="6.5" stroke="#F5D76E" stroke-width="1.5"/>
    <path d="M5 6h4M5 8h6M5 10h3" stroke="#F5D76E" stroke-width="1.3" stroke-linecap="round"/>
  </svg>`,
};

class ExeServiceSwitcher extends HTMLElement {
  static get observedAttributes() {
    return ["current", "user", "base-url"];
  }

  constructor() {
    super();
    this._shadow = this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    this._render();
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
          <span class="exe-ss-icon">${ICONS[s.icon]}</span>
          <span class="exe-ss-label">${s.label}</span>
        </a>
      `;
    }).join('<span class="exe-ss-sep" aria-hidden="true"></span>');

    const userSection = user
      ? `
        <div class="exe-ss-user">
          <span class="exe-ss-email">${this._escapeHtml(user)}</span>
          <a href="https://${domain}/auth/logout" class="exe-ss-logout" title="Sign out">
            ${ICONS.logout}
          </a>
        </div>
      `
      : "";

    this._shadow.innerHTML = `
      <style>${this._styles()}</style>
      <nav class="exe-ss-bar" aria-label="Exe service navigation">
        <div class="exe-ss-left">
          <span class="exe-ss-logo" aria-hidden="true">${ICONS.exe}</span>
          ${serviceLinks}
        </div>
        <div class="exe-ss-right">
          ${userSection}
        </div>
      </nav>
    `;
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
        opacity: 0.7;
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

      .exe-ss-icon {
        display: inline-flex;
        align-items: center;
        flex-shrink: 0;
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
