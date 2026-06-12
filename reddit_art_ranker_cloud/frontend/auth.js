/* Mini Art Salon · Google Sign-In (direct GIS — no Cognito).
 *
 * Loads the ID token (a JWT, ~1h life) into localStorage, renders a sign-in
 * button or a signed-in chip into every `.auth-slot`, and exposes a small API.
 * Requires app.js (apiJson) and the GIS client script to be on the page.
 *
 *   MAS_AUTH.token()               -> current valid ID token, or null
 *   MAS_AUTH.user()                -> {sub,email,name,picture} from the JWT, or null
 *   MAS_AUTH.authedJson(url, opts) -> apiJson + `Authorization: Bearer`; on 401
 *                                     it signs out (token expired) and rethrows
 *   MAS_AUTH.onChange(fn)          -> fn(user|null) now and on every change
 *   MAS_AUTH.signOut()
 *
 * Config via window.GOOGLE_CLIENT_ID (see config.js). Unset => sign-in hidden,
 * everything stays anonymous (matches the backend, which disables auth then).
 */
(function () {
  "use strict";

  var KEY = "mas_id_token";
  var CLIENT_ID = window.GOOGLE_CLIENT_ID || "";
  var listeners = [];

  // ── token helpers ──
  function decode(token) {
    try {
      var payload = token.split(".")[1];
      var json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
      return JSON.parse(decodeURIComponent(escape(json)));
    } catch (e) { return null; }
  }
  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function claims() {
    var c = decode(stored() || "");
    // 5s skew so we don't hand the API a token that's about to expire.
    return c && c.exp && c.exp * 1000 > Date.now() + 5000 ? c : null;
  }
  function token() { return claims() ? stored() : null; }
  function user() {
    var c = claims();
    return c ? { sub: c.sub, email: c.email || "", name: c.name || "", picture: c.picture || "" } : null;
  }

  function notify() {
    var u = user();
    listeners.forEach(function (fn) { try { fn(u); } catch (e) {} });
  }
  function setToken(t) {
    try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY); } catch (e) {}
    renderSlots();
    notify();
  }
  function onCredential(resp) { if (resp && resp.credential) setToken(resp.credential); }

  function signOut() {
    setToken(null);
    if (window.google && google.accounts && google.accounts.id) {
      google.accounts.id.disableAutoSelect();
    }
  }

  // apiJson + bearer header. A 401 means the token lapsed -> sign out so the UI
  // drops back to the signed-out state and the user can re-auth.
  function authedJson(url, opts) {
    opts = opts || {};
    var headers = Object.assign({}, opts.headers || {});
    var t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    opts.headers = headers;
    return apiJson(url, opts).catch(function (e) {
      if (/(^|\D)401(\D|$)/.test(e.message)) signOut();
      throw e;
    });
  }

  // ── UI: render into every .auth-slot ──
  function renderSlots() {
    var slots = document.querySelectorAll(".auth-slot");
    if (!slots.length) return;
    var u = user();
    slots.forEach(function (slot) {
      slot.innerHTML = "";
      if (!CLIENT_ID) return; // auth disabled -> nothing to show
      if (u) {
        var chip = document.createElement("div");
        chip.className = "auth-chip";
        chip.innerHTML =
          (u.picture ? '<img src="' + u.picture + '" alt="" referrerpolicy="no-referrer">' : "") +
          '<a href="mysalon.html">My salon</a>' +
          '<button class="auth-out" type="button" title="Sign out">Sign out</button>';
        chip.querySelector(".auth-out").addEventListener("click", signOut);
        slot.appendChild(chip);
      } else {
        var holder = document.createElement("div");
        holder.className = "auth-btn-holder";
        slot.appendChild(holder);
        if (window.google && google.accounts && google.accounts.id) {
          google.accounts.id.renderButton(holder, {
            type: "standard", theme: "outline", size: "medium",
            text: "signin_with", shape: "pill",
          });
        }
      }
    });
  }

  function init() {
    if (!CLIENT_ID) { renderSlots(); return; }
    // GIS client loads async — retry until google.accounts.id is ready.
    if (!(window.google && google.accounts && google.accounts.id)) {
      return void setTimeout(init, 120);
    }
    google.accounts.id.initialize({
      client_id: CLIENT_ID, callback: onCredential, auto_select: true,
    });
    renderSlots();
  }

  window.MAS_AUTH = {
    token: token,
    user: user,
    authedJson: authedJson,
    signOut: signOut,
    onChange: function (fn) { listeners.push(fn); fn(user()); },
    // Re-render sign-in widgets after a page injects a new .auth-slot.
    refresh: renderSlots,
  };

  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
