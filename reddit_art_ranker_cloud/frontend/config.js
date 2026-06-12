/* Mini Art Salon · shared front-end config. Loaded first on every page so
 * API_URL and the Google client ID live in exactly one place. */
window.API_URL = window.API_URL || "https://gt5jcvia2l.execute-api.us-east-1.amazonaws.com";

// Google OAuth 2.0 *Web* client ID (from Google Cloud Console). Must match the
// GOOGLE_CLIENT_ID set on the API Lambda. Leave "" to keep the app fully
// anonymous (the sign-in button is hidden and salons are inaccessible) until
// the OAuth client exists.
window.GOOGLE_CLIENT_ID = window.GOOGLE_CLIENT_ID || "";
