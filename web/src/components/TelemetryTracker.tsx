/** 路由变化时静默上报 page_view(无视觉/交互影响)。 */
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { pageView } from '../telemetry';

export default function TelemetryTracker() {
  const location = useLocation();
  const lastFired = useRef<{ path: string; at: number } | null>(null);
  useEffect(() => {
    const path = location.pathname;
    // React StrictMode 开发期会双调 effect; 同一路径短窗口内只上报一次, 避免重复计数
    const now = Date.now();
    if (lastFired.current && lastFired.current.path === path && now - lastFired.current.at < 800) return;
    lastFired.current = { path, at: now };
    pageView(path);
  }, [location.pathname]);
  return null;
}
