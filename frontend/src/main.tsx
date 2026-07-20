import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import App from './App';
import './styles.css';
import './styles/globals.css';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Root element #root was not found.');
}

// 用 ?ui=preview 打开新 UI 底座的预览页（PR-A 阶段的可视验证入口，
// 后续 PR-B 迁移完毕即可删除）
const params =
  typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
const showPreview = params?.get('ui') === 'preview';

async function bootstrap() {
  if (showPreview) {
    const { UiPreview } = await import('./preview/UiPreview');
    ReactDOM.createRoot(rootElement!).render(
      <React.StrictMode>
        <BrowserRouter>
          <UiPreview />
        </BrowserRouter>
      </React.StrictMode>,
    );
    return;
  }

  ReactDOM.createRoot(rootElement!).render(
    <React.StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </React.StrictMode>,
  );
}

void bootstrap();
