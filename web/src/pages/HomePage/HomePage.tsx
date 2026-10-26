import { HeroSection } from '../../widgets/HeroSection/HeroSection'
import { HowItWorksSection } from '../../widgets/HowItWorksSection/HowItWorksSection'
import { PlannerSection } from '../../widgets/PlannerSection/PlannerSection'
import { SiteFooter } from '../../widgets/SiteFooter/SiteFooter'
import { SiteHeader } from '../../widgets/SiteHeader/SiteHeader'

export function HomePage() {
  return (
    <div id="top">
      <SiteHeader />
      <main>
        <HeroSection />
        <PlannerSection />
        <HowItWorksSection />
      </main>
      <SiteFooter />
    </div>
  )
}
