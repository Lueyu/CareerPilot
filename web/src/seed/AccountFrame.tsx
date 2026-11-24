import type { ReactNode } from 'react';
import LanguageSelector from '../components/LanguageSelector';
import OnboardingBrand from '../components/OnboardingBrand';

export function AccountFrame({ children }: { children: ReactNode }) {
  return <main className="account-entry">
    <div className="account-entry-header">
      <OnboardingBrand />
      <LanguageSelector />
    </div>
    <div className="account-entry-content">{children}</div>
  </main>;
}
