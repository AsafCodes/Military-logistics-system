import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import ErrorBoundary from './components/shared/ErrorBoundary.tsx'
import { removeLocal } from './lib/safeStorage'
import { OpenAPI } from './client';

OpenAPI.BASE = 'http://127.0.0.1:8000';

// SEC-H9 one-time scrub. Every browser that used this application before the
// session moved into an httpOnly cookie still has a bearer token sitting in
// localStorage, valid until it expires and readable by any script on the page.
// Nothing reads these keys any more, so without this the vulnerability the
// ticket describes would simply persist in existing browsers after the fix
// shipped.
//
// REMOVE AFTER 2027-03-01. This is a one-time migration with no expiry of its
// own, so without a date it runs on every page load forever and nobody can tell
// whether it is still earning its place. Any browser that has not loaded the app
// in six months has long since expired the 30-minute token it might be holding.
//
// Via removeLocal because this runs at module scope, BEFORE createRoot()
// .render(): a throw here kills the boot while the ErrorBoundary below is not
// yet in the tree to catch it. See lib/safeStorage for why the accessor itself
// can throw. A browser that cannot read storage cannot be holding a leaked
// token either, so there is nothing to recover from.
removeLocal('token');
removeLocal('user');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
