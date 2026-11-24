import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import './styles/tokens.css';
import './styles/base.css';
import './styles/components.css';
import './styles/mobile.css';
import App from './workspace-entry';
import { currentTheme } from './components/Topbar';
import { initializeDocumentLocale } from './i18n';

document.documentElement.dataset.theme = currentTheme();
initializeDocumentLocale();

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
