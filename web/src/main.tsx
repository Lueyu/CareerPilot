import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles/tokens.css';
import './styles/base.css';
import './styles/components.css';
import './pages/memory.css';
import './pages/bank.css';
import './pages/dailyLearningPlan.css';
import './pages/projectLibrary.css';
import './pages/projectReview.css';
import './components/projectResume.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
