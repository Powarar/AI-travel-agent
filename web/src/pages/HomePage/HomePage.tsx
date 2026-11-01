import { HeroSection } from '../../widgets/HeroSection/HeroSection'
import { HowItWorksSection } from '../../widgets/HowItWorksSection/HowItWorksSection'
import { PlannerSection } from '../../widgets/PlannerSection/PlannerSection'
import { SiteFooter } from '../../widgets/SiteFooter/SiteFooter'
import { SiteHeader } from '../../widgets/SiteHeader/SiteHeader'

export function HomePage({ onStart }: { onStart: (prompt: string) => void }) {
  return (
    <div id="top">
      <SiteHeader />
      <main>
        <HeroSection />
        <PlannerSection onStart={onStart} />
        <HowItWorksSection />
      </main>
      <SiteFooter />
    </div>
  )
}
