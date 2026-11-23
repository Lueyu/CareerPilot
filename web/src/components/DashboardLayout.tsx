import type { ReactNode } from 'react';

export function DashboardHero({ label, title, badge, summary, score, children, footer }: {
  label: string;
  title: string;
  badge?: string;
  summary: string;
  score?: ReactNode;
  children?: ReactNode;
  footer: ReactNode;
}) {
  return (
    <div className="hero-card" style={{ marginBottom: 16 }}>
      <div className="hero-top">
        <div>
          <div className="hero-label">{label}</div>
          <div className="hero-title">
            {title}
            {badge && <span className="hero-chip">{badge}</span>}
          </div>
          <div className="hero-plain">{summary}</div>
        </div>
        {score && <div className="hero-score">{score}</div>}
      </div>
      {children && <div className="hero-body">{children}</div>}
      <div className="hero-foot">{footer}</div>
    </div>
  );
}

export function DashboardStats({ cards }: {
  cards: { label: string; value: string | number; ic: string; delta?: string }[];
}) {
  return (
    <div className="stat-row">
      {cards.map(card => (
        <div className="stat-mini" key={card.label}>
          <div className="stat-mini-label">{card.ic} {card.label}</div>
          <div className="stat-mini-value">
            {card.value}{card.delta && <small className="stat-delta delta-up">{card.delta}</small>}
          </div>
        </div>
      ))}
    </div>
  );
}

export function DashboardCard({ title, action, children }: {
  title: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">{title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}
