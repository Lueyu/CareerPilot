import type { ReactNode } from 'react';
import OnboardingBrand from '../components/OnboardingBrand';

export function AccountFrame({ children }: { children: ReactNode }) {
  return <main className="account-entry">
    <OnboardingBrand />
    <div className="account-entry-content">{children}</div>
  </main>;
}
