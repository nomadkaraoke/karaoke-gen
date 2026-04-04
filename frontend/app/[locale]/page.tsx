'use client';

import { useState, useEffect } from 'react';
import { useRouter } from '@/i18n/routing';
import { useTranslations, useLocale } from 'next-intl';
import Image from 'next/image';
import {
  Music,
  Sparkles,
  Video,
  Youtube,
  Check,
  Gift,
  Mail,
  Loader2,
  Search,
  Link as LinkIcon,
  Upload,
  Clock,
  FileVideo,
  CreditCard,
} from 'lucide-react';
import { api, setAccessToken, getAccessToken, CreditPackage } from '@/lib/api';
import { AuthDialog } from '@/components/auth/AuthDialog';
import { WarmingUpLoader } from '@/components/WarmingUpLoader';
import { ThemeToggle } from '@/components/ThemeToggle';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import { useTenant } from '@/lib/tenant';
import { TenantLandingPage } from '@/components/TenantLandingPage';

// Build-time constant: when true, this build is for tenant portals only
const IS_TENANT_PORTAL = process.env.NEXT_PUBLIC_TENANT_PORTAL === 'true';

export default function LandingPage() {
  // Tenant portal builds always render the tenant landing page
  // This is a build-time constant so the consumer code gets tree-shaken
  if (IS_TENANT_PORTAL) {
    return <TenantLandingPage />;
  }

  return <ConsumerLandingPage />;
}

function ConsumerLandingPage() {
  const router = useRouter();
  const t = useTranslations('landing');
  const locale = useLocale();
  const { isDefault: isDefaultTenant, isLoading: tenantLoading, isInitialized: tenantInitialized } = useTenant();
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [isMounted, setIsMounted] = useState(false);
  const [packages, setPackages] = useState<CreditPackage[]>([]);
  const [packagesLoading, setPackagesLoading] = useState(true);
  const [packagesError, setPackagesError] = useState<string | null>(null);
  const [selectedPackage, setSelectedPackage] = useState<CreditPackage | null>(null);
  const [email, setEmail] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Made For You form state
  const [mfyArtist, setMfyArtist] = useState('');
  const [mfyTitle, setMfyTitle] = useState('');
  const [mfySourceType, setMfySourceType] = useState<'search' | 'youtube' | 'upload'>('search');
  const [mfyYoutubeUrl, setMfyYoutubeUrl] = useState('');
  const [mfyEmail, setMfyEmail] = useState('');
  const [mfyNotes, setMfyNotes] = useState('');
  const [mfyLoading, setMfyLoading] = useState(false);
  const [mfyError, setMfyError] = useState<string | null>(null);

  // Auth dialog state
  const [showAuthDialog, setShowAuthDialog] = useState(false);

  // FAQ data (uses translation keys)
  const faqs = [
    { question: t('faq.faq1Question'), answer: t('faq.faq1Answer') },
    { question: t('faq.faq2Question'), answer: t('faq.faq2Answer') },
    { question: t('faq.faq3Question'), answer: t('faq.faq3Answer') },
    { question: t('faq.faq4Question'), answer: t('faq.faq4Answer') },
    { question: t('faq.faq5Question'), answer: t('faq.faq5Answer') },
  ];

  // Track client-side mount to avoid hydration mismatch
  useEffect(() => {
    setIsMounted(true);
  }, []);

  // Check if already logged in and redirect to app
  useEffect(() => {
    const token = getAccessToken();
    if (token) {
      router.replace('/app');
      return;
    }
    setIsCheckingAuth(false);
  }, [router]);

  // Handle successful auth from dialog
  const handleAuthSuccess = () => {
    setShowAuthDialog(false);
    router.push('/app');
  };

  // Load credit packages on mount
  useEffect(() => {
    if (isCheckingAuth) return;

    async function loadPackages() {
      try {
        const pkgs = await api.getCreditPackages();
        setPackages(pkgs);
        // Default to 5 credits (best value)
        const bestValue = pkgs.find((p) => p.credits === 5);
        setSelectedPackage(bestValue || pkgs[0]);
      } catch (err) {
        console.error('Failed to load packages:', err);
        setPackagesError('Failed to load pricing. Please refresh the page.');
      } finally {
        setPackagesLoading(false);
      }
    }
    loadPackages();
  }, [isCheckingAuth]);

  // Tenant portals get a minimal landing page (just logo + sign in)
  // Only check after mount to avoid hydration mismatch with static export
  // Must be AFTER all hooks to comply with React Rules of Hooks
  if (isMounted && tenantInitialized && !isDefaultTenant) {
    return <TenantLandingPage />;
  }

  // Handle checkout
  const handleCheckout = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedPackage || !email) return;

    setIsLoading(true);
    setError(null);

    try {
      const checkoutUrl = await api.createCheckout(selectedPackage.id, email);
      // Validate that the URL is a Stripe checkout URL
      if (!checkoutUrl.startsWith('https://checkout.stripe.com/')) {
        throw new Error('Invalid checkout URL received');
      }
      window.location.href = checkoutUrl;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create checkout session');
      setIsLoading(false);
    }
  };

  // Handle Made For You checkout
  const handleMadeForYouSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setMfyError(null);

    // Validation
    if (!mfyArtist.trim()) {
      setMfyError(t('madeForYou.artistRequired'));
      return;
    }
    if (!mfyTitle.trim()) {
      setMfyError(t('madeForYou.titleRequired'));
      return;
    }
    if (!mfyEmail.trim()) {
      setMfyError(t('madeForYou.emailRequired'));
      return;
    }
    if (mfySourceType === 'youtube' && !mfyYoutubeUrl.trim()) {
      setMfyError(t('madeForYou.youtubeUrlRequired'));
      return;
    }

    setMfyLoading(true);

    try {
      const checkoutUrl = await api.createMadeForYouCheckout({
        email: mfyEmail,
        artist: mfyArtist,
        title: mfyTitle,
        source_type: mfySourceType,
        youtube_url: mfySourceType === 'youtube' ? mfyYoutubeUrl : undefined,
        notes: mfyNotes || undefined,
      });

      // Redirect to Stripe Checkout
      window.location.href = checkoutUrl;
    } catch (err) {
      setMfyError(err instanceof Error ? err.message : t('madeForYou.submitError'));
      setMfyLoading(false);
    }
  };

  const formatPrice = (cents: number) => `$${(cents / 100).toFixed(0)}`;
  const pricePerVideo = (pkg: CreditPackage) =>
    `$${(pkg.price_cents / 100 / pkg.credits).toFixed(2)}`;

  // Show loading while checking auth
  if (isCheckingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center animated-gradient">
        <Loader2 className="w-8 h-8 animate-spin text-white" />
      </div>
    );
  }

  return (
    <div className="min-h-screen animated-gradient">
      {/* Navigation */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-dark-900/80 backdrop-blur-md border-b border-dark-700">
        <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" className="h-10" />
          </div>
          <div className="flex items-center gap-3">
            <LanguageSwitcher />
            <ThemeToggle />
            <button
              onClick={() => setShowAuthDialog(true)}
              className="px-4 py-2 text-sm font-semibold rounded-lg bg-[var(--brand-pink)] text-white hover:bg-[var(--brand-pink-hover)] hover:scale-105 hover:shadow-[0_0_20px_rgba(255,122,204,0.5)] active:scale-95 transition-all duration-200"
            >
              {t('navigation.signIn')}
            </button>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="pt-32 pb-6 px-4">
        <div className="max-w-4xl mx-auto text-center">
          <h1 className="text-4xl sm:text-5xl md:text-6xl font-extrabold mb-6 leading-tight">
            {t('hero.title')}
          </h1>
          <p className="text-xl text-dark-300 mb-8 max-w-2xl mx-auto">
            {t('hero.subtitle')}
          </p>

          {/* Two pricing options */}
          <div className="flex flex-col sm:flex-row items-center justify-center gap-6 mb-6">
            <div className="flex flex-col items-center gap-2">
              <button
                onClick={() => setShowAuthDialog(true)}
                className="inline-flex items-center gap-2 bg-primary-500 hover:bg-primary-600 text-white font-semibold px-8 py-4 rounded-xl transition-all btn-glow"
              >
                <Video className="w-5 h-5" />
                {t('heroCTA.createYourself')}
                <span className="ml-1 px-2 py-0.5 bg-white/20 rounded-full text-sm">{t('heroCTA.createYourselfPrice')}</span>
              </button>
              <span className="text-green-400 text-sm">{t('heroCTA.createYourselfNote')}</span>
            </div>
            <div className="flex flex-col items-center gap-2">
              <a
                href="#made-for-you"
                className="inline-flex items-center gap-2 bg-yellow-500/20 hover:bg-yellow-500/30 text-yellow-400 border border-yellow-500/40 font-semibold px-8 py-4 rounded-xl transition-all"
              >
                <Gift className="w-5 h-5" />
                {t('heroCTA.makeForYou')}
                <span className="ml-1 px-2 py-0.5 bg-yellow-500/20 rounded-full text-sm">{t('heroCTA.makeForYouPrice')}</span>
              </a>
              <span className="text-yellow-400/70 text-sm">{t('heroCTA.makeForYouNote')}</span>
            </div>
          </div>

        </div>
      </section>

      {/* Demo Video Section */}
      <section className="py-8 px-4">
        <div className="max-w-4xl mx-auto">
          <div className="bg-dark-800 border border-dark-700 rounded-2xl p-2 overflow-hidden">
            <div className="aspect-video">
              <iframe
                className="w-full h-full rounded-xl"
                src="https://www.youtube.com/embed/RLkIEHGwFcU"
                title={t('demoVideo.title')}
                frameBorder="0"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                referrerPolicy="strict-origin-when-cross-origin"
                allowFullScreen
              />
            </div>
          </div>
        </div>
      </section>

      {/* How It Works */}
      <section className="py-16 px-4">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-12">{t('howItWorks.title')}</h2>
          <div className="grid md:grid-cols-4 gap-8">
            {[
              { icon: Music, title: t('howItWorks.step1Title'), desc: t('howItWorks.step1Desc') },
              { icon: Sparkles, title: t('howItWorks.step2Title'), desc: t('howItWorks.step2Desc') },
              { icon: Video, title: t('howItWorks.step3Title'), desc: t('howItWorks.step3Desc') },
              { icon: Youtube, title: t('howItWorks.step4Title'), desc: t('howItWorks.step4Desc') },
            ].map((step, i) => (
              <div key={i} className="text-center">
                <div className="w-16 h-16 bg-primary-500/20 rounded-2xl flex items-center justify-center mx-auto mb-4">
                  <step.icon className="w-8 h-8 text-primary-400" />
                </div>
                <h3 className="font-semibold text-lg mb-2">{step.title}</h3>
                <p className="text-dark-400 text-sm">{step.desc}</p>
              </div>
            ))}
          </div>

          {/* Full Tutorial Video */}
          <div className="mt-12 text-center">
            <h3 className="text-xl font-semibold mb-2">{t('howItWorks.tutorialTitle')}</h3>
            <p className="text-dark-400 text-sm mb-6 max-w-xl mx-auto">
              {t('howItWorks.tutorialDesc')}
            </p>
            <div className="max-w-3xl mx-auto">
              <div className="bg-dark-800 border border-dark-700 rounded-2xl p-2 overflow-hidden">
                <div className="aspect-video">
                  <iframe
                    className="w-full h-full rounded-xl"
                    src="https://www.youtube.com/embed/-dI3r7qXo3A"
                    title={t('howItWorks.tutorialVideoTitle')}
                    frameBorder="0"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                    referrerPolicy="strict-origin-when-cross-origin"
                    allowFullScreen
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-16 px-4 bg-dark-800/50">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-12">{t('features.title')}</h2>
          <div className="grid md:grid-cols-3 gap-6">
            {[
              { title: t('features.feature1Title'), desc: t('features.feature1Desc') },
              { title: t('features.feature2Title'), desc: t('features.feature2Desc') },
              { title: t('features.feature3Title'), desc: t('features.feature3Desc') },
              { title: t('features.feature4Title'), desc: t('features.feature4Desc') },
              { title: t('features.feature5Title'), desc: t('features.feature5Desc') },
              { title: t('features.feature6Title'), desc: t('features.feature6Desc') },
            ].map((feature, i) => (
              <div key={i} className="bg-dark-800 border border-dark-700 rounded-xl p-6 card-hover">
                <h3 className="font-semibold text-lg mb-2">{feature.title}</h3>
                <p className="text-dark-400 text-sm">{feature.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Screenshots Section */}
      <section className="py-16 px-4">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-4">{t('screenshots.title')}</h2>
          <p className="text-dark-400 text-center mb-12 max-w-xl mx-auto">
            {t('screenshots.subtitle')}
          </p>
          <div className="grid md:grid-cols-2 gap-8">
            {/* Job Creation Screenshot */}
            <div className="bg-dark-800 border border-dark-700 rounded-xl overflow-hidden">
              <div className="aspect-video bg-dark-900 relative">
                <Image
                  src="/screenshots/job-dashboard.avif"
                  alt={t('screenshots.jobCreationAlt')}
                  fill
                  className="object-cover"
                  sizes="(max-width: 768px) 100vw, 50vw"
                />
              </div>
              <div className="p-4">
                <h3 className="font-semibold mb-1">{t('screenshots.jobCreationTitle')}</h3>
                <p className="text-dark-400 text-sm">{t('screenshots.jobCreationDesc')}</p>
              </div>
            </div>

            {/* Lyrics Review Screenshot */}
            <div className="bg-dark-800 border border-dark-700 rounded-xl overflow-hidden">
              <div className="aspect-video bg-dark-900 relative">
                <Image
                  src="/screenshots/lyrics-review.avif"
                  alt={t('screenshots.lyricsReviewAlt')}
                  fill
                  className="object-cover object-top"
                  sizes="(max-width: 768px) 100vw, 50vw"
                />
              </div>
              <div className="p-4">
                <h3 className="font-semibold mb-1">{t('screenshots.lyricsReviewTitle')}</h3>
                <p className="text-dark-400 text-sm">{t('screenshots.lyricsReviewDesc')}</p>
              </div>
            </div>

            {/* Instrumental Selection Screenshot */}
            <div className="bg-dark-800 border border-dark-700 rounded-xl overflow-hidden">
              <div className="aspect-video bg-dark-900 relative">
                <Image
                  src="/screenshots/instrumental-review.avif"
                  alt={t('screenshots.instrumentalReviewAlt')}
                  fill
                  className="object-cover object-top"
                  sizes="(max-width: 768px) 100vw, 50vw"
                />
              </div>
              <div className="p-4">
                <h3 className="font-semibold mb-1">{t('screenshots.instrumentalReviewTitle')}</h3>
                <p className="text-dark-400 text-sm">{t('screenshots.instrumentalReviewDesc')}</p>
              </div>
            </div>

            {/* Example Output Screenshot */}
            <div className="bg-dark-800 border border-dark-700 rounded-xl overflow-hidden">
              <div className="aspect-video bg-dark-900 relative">
                <Image
                  src="/screenshots/example-output.avif"
                  alt={t('screenshots.exampleOutputAlt')}
                  fill
                  className="object-cover"
                  sizes="(max-width: 768px) 100vw, 50vw"
                />
              </div>
              <div className="p-4">
                <h3 className="font-semibold mb-1">{t('screenshots.exampleOutputTitle')}</h3>
                <p className="text-dark-400 text-sm">{t('screenshots.exampleOutputDesc')}</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="py-20 px-4">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-4">{t('pricing.title')}</h2>
          <p className="text-dark-400 text-center mb-4 max-w-xl mx-auto">
            {t('pricing.subtitle')}
          </p>

          <div className="bg-green-500/10 border border-green-500/30 rounded-2xl p-6 text-center mb-10 max-w-sm mx-auto">
            <div className="text-sm text-green-400 font-medium mb-1">{t('pricing.freeCreditLabel')}</div>
            <div className="text-4xl font-bold text-green-400 mb-1">{t('pricing.freeCredit')}</div>
            <div className="text-sm text-dark-400 mb-4">{t('pricing.freeCreditNote')}</div>
            <button
              onClick={() => setShowAuthDialog(true)}
              className="inline-flex items-center gap-2 bg-green-500 hover:bg-green-600 text-white font-semibold px-6 py-3 rounded-xl transition-all"
            >
              {t('pricing.signUpFree')}
            </button>
          </div>

          {/* Package Selection */}
          {packagesLoading && (
            <WarmingUpLoader className="py-8" />
          )}
          {packagesError && (
            <div className="text-center py-8 text-red-400">
              {packagesError}
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            {packages.map((pkg) => {
              const isSelected = selectedPackage?.id === pkg.id;
              const isBestValue = pkg.credits === 5;
              const savings = pkg.credits > 1 ? Math.round((1 - pkg.price_cents / (pkg.credits * 500)) * 100) : 0;

              return (
                <button
                  key={pkg.id}
                  onClick={() => setSelectedPackage(pkg)}
                  className={`relative p-4 rounded-xl border-2 transition-all ${
                    isSelected
                      ? 'border-primary-500 bg-primary-500/10'
                      : 'border-dark-700 hover:border-dark-500 bg-dark-800/50'
                  }`}
                >
                  {isBestValue && (
                    <span className="absolute -top-3 left-1/2 -translate-x-1/2 bg-primary-500 text-white text-xs font-semibold px-3 py-1 rounded-full">
                      {t('pricing.bestValue')}
                    </span>
                  )}
                  <div className="text-2xl font-bold mb-1">{pkg.credits}</div>
                  <div className="text-sm text-dark-400 mb-2">
                    {pkg.credits === 1 ? t('pricing.creditUnit') : t('pricing.creditsUnit')}
                  </div>
                  <div className="text-xl font-semibold text-primary-400">
                    {formatPrice(pkg.price_cents)}
                  </div>
                  <div className="text-xs text-dark-500">{pricePerVideo(pkg)}{t('pricing.perVideo')}</div>
                  {savings > 0 && (
                    <div className="text-xs text-green-400 mt-1">{t('pricing.saveFmt', { savings })}</div>
                  )}
                </button>
              );
            })}
          </div>

          {/* Checkout Form */}
          <div className="bg-dark-800 border border-dark-700 rounded-2xl p-8 max-w-md mx-auto">
            <div className="text-center mb-6">
              <div className="text-sm text-dark-400 mb-1">{t('pricing.selectedPackage')}</div>
              <div className="text-2xl font-bold">
                {selectedPackage?.credits} {selectedPackage && selectedPackage.credits === 1 ? t('pricing.creditUnit') : t('pricing.creditsUnit')}
              </div>
              <div className="text-3xl font-bold text-primary-400">
                {selectedPackage && formatPrice(selectedPackage.price_cents)}
              </div>
            </div>

            <form onSubmit={handleCheckout} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-dark-300 mb-2">
                  {t('pricing.emailLabel')}
                </label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-muted-foreground" />
                  <input
                    type="email"
                    id="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder={t('pricing.emailPlaceholder')}
                    className="w-full pl-10 pr-4 py-3 bg-secondary border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent text-foreground placeholder:text-muted-foreground"
                    required
                  />
                </div>
                <p className="text-xs text-dark-500 mt-2">
                  {t('pricing.emailHint')}
                </p>
              </div>

              {error && <p className="text-red-400 text-sm">{error}</p>}

              <button
                type="submit"
                disabled={isLoading || !selectedPackage}
                className="w-full bg-primary-500 hover:bg-primary-600 disabled:bg-primary-500/50 text-white font-semibold py-4 rounded-xl transition-all btn-glow flex items-center justify-center gap-2"
              >
                {isLoading ? t('pricing.redirecting') : t('pricing.continuePayment')}
                {!isLoading && <Check className="w-5 h-5" />}
              </button>
            </form>

            <div className="mt-4 flex items-center justify-center gap-4 text-xs text-dark-500">
              <span>{t('pricing.secureCheckout')}</span>
              <span>{t('pricing.poweredByStripe')}</span>
            </div>
          </div>
        </div>
      </section>

      {/* Made For You Section */}
      <section id="made-for-you" className="py-20 px-4">
        <div className="max-w-6xl mx-auto">
          {/* Section Header */}
          <div className="text-center mb-12">
            <div className="inline-flex items-center gap-2 bg-yellow-500/20 text-yellow-400 px-4 py-2 rounded-full text-sm font-medium mb-4">
              <Gift className="w-4 h-4" />
              {t('madeForYou.badgeLabel')}
            </div>
            <h2 className="text-3xl font-bold mb-4">{t('madeForYou.title')}</h2>
            <p className="text-dark-400 max-w-2xl mx-auto mb-4">
              {t('madeForYou.subtitle')}
            </p>
            <p className="text-dark-500 text-sm max-w-xl mx-auto">
              {t('madeForYou.secondarySubtitle')}
            </p>
          </div>

          <div className="grid lg:grid-cols-2 gap-12 items-start">
            {/* Benefits */}
            <div>
              <h3 className="text-xl font-semibold mb-6">{t('madeForYou.benefitsTitle')}</h3>
              <div className="space-y-4">
                <div className="bg-dark-800 border border-dark-700 rounded-xl p-4 flex items-start gap-4">
                  <div className="w-10 h-10 bg-primary-500/20 rounded-lg flex items-center justify-center shrink-0">
                    <FileVideo className="w-5 h-5 text-primary-400" />
                  </div>
                  <div>
                    <h4 className="font-medium">{t('madeForYou.benefit1Title')}</h4>
                    <p className="text-dark-400 text-sm mt-1">
                      {t('madeForYou.benefit1Desc')}
                    </p>
                  </div>
                </div>
                <div className="bg-dark-800 border border-dark-700 rounded-xl p-4 flex items-start gap-4">
                  <div className="w-10 h-10 bg-purple-500/20 rounded-lg flex items-center justify-center shrink-0">
                    <Clock className="w-5 h-5 text-purple-400" />
                  </div>
                  <div>
                    <h4 className="font-medium">{t('madeForYou.benefit2Title')}</h4>
                    <p className="text-dark-400 text-sm mt-1">
                      {t('madeForYou.benefit2Desc')}
                    </p>
                  </div>
                </div>
                <div className="bg-dark-800 border border-dark-700 rounded-xl p-4 flex items-start gap-4">
                  <div className="w-10 h-10 bg-yellow-500/20 rounded-lg flex items-center justify-center shrink-0">
                    <Gift className="w-5 h-5 text-yellow-400" />
                  </div>
                  <div>
                    <h4 className="font-medium">{t('madeForYou.benefit3Title')}</h4>
                    <p className="text-dark-400 text-sm mt-1">
                      {t('madeForYou.benefit3Desc')}
                    </p>
                  </div>
                </div>
                <a
                  href="https://www.youtube.com/@nomadkaraoke"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="bg-dark-800 border border-dark-700 rounded-xl p-4 flex items-start gap-4 hover:border-red-500/40 transition-colors group"
                >
                  <div className="w-10 h-10 bg-red-500/20 rounded-lg flex items-center justify-center shrink-0">
                    <Youtube className="w-5 h-5 text-red-400" />
                  </div>
                  <div>
                    <h4 className="font-medium group-hover:text-red-400 transition-colors">{t('madeForYou.benefit4Title')}</h4>
                    <p className="text-dark-400 text-sm mt-1">
                      {t('madeForYou.benefit4Desc')}
                    </p>
                  </div>
                </a>
              </div>

              {/* Delivery includes */}
              <div className="mt-8 p-4 bg-dark-800 border border-dark-700 rounded-xl">
                <h4 className="font-medium mb-3">{t('madeForYou.deliveryIncludesTitle')}</h4>
                <ul className="space-y-2 text-sm text-dark-400">
                  <li className="flex items-center gap-2">
                    <Check className="w-4 h-4 text-green-400" />
                    {t('madeForYou.delivery1')}
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="w-4 h-4 text-green-400" />
                    {t('madeForYou.delivery2')}
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="w-4 h-4 text-green-400" />
                    {t('madeForYou.delivery3')}
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="w-4 h-4 text-green-400" />
                    {t('madeForYou.delivery4')}
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="w-4 h-4 text-green-400" />
                    {t('madeForYou.delivery5')}
                  </li>
                </ul>
              </div>
            </div>

            {/* Order Form */}
            <div className="bg-dark-800 border border-dark-700 rounded-2xl p-6 sm:p-8">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-xl font-semibold">{t('madeForYou.orderFormTitle')}</h3>
                <div className="text-2xl font-bold text-primary-400">{t('madeForYou.orderPrice')}</div>
              </div>

              <form onSubmit={handleMadeForYouSubmit} className="space-y-5">
                {/* Artist */}
                <div>
                  <label htmlFor="mfy-artist" className="block text-sm font-medium text-dark-300 mb-2">
                    {t('madeForYou.artistLabel')} <span className="text-red-400">{t('madeForYou.requiredField')}</span>
                  </label>
                  <input
                    type="text"
                    id="mfy-artist"
                    value={mfyArtist}
                    onChange={(e) => setMfyArtist(e.target.value)}
                    placeholder={t('madeForYou.artistPlaceholder')}
                    className="w-full px-4 py-3 bg-secondary border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent text-foreground placeholder:text-muted-foreground"
                  />
                </div>

                {/* Title */}
                <div>
                  <label htmlFor="mfy-title" className="block text-sm font-medium text-dark-300 mb-2">
                    {t('madeForYou.titleLabel')} <span className="text-red-400">{t('madeForYou.requiredField')}</span>
                  </label>
                  <input
                    type="text"
                    id="mfy-title"
                    value={mfyTitle}
                    onChange={(e) => setMfyTitle(e.target.value)}
                    placeholder={t('madeForYou.titlePlaceholder')}
                    className="w-full px-4 py-3 bg-secondary border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent text-foreground placeholder:text-muted-foreground"
                  />
                </div>

                {/* Source Type */}
                <div>
                  <label className="block text-sm font-medium text-dark-300 mb-2">
                    {t('madeForYou.audioSourceLabel')}
                  </label>
                  <div className="grid grid-cols-3 gap-2">
                    <button
                      type="button"
                      onClick={() => setMfySourceType('search')}
                      className={`flex flex-col items-center gap-1 p-3 rounded-lg border transition-all ${
                        mfySourceType === 'search'
                          ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                          : 'border-dark-700 text-dark-400 hover:border-dark-500'
                      }`}
                    >
                      <Search className="w-5 h-5" />
                      <span className="text-xs">{t('madeForYou.autoSearchLabel')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setMfySourceType('youtube')}
                      className={`flex flex-col items-center gap-1 p-3 rounded-lg border transition-all ${
                        mfySourceType === 'youtube'
                          ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                          : 'border-dark-700 text-dark-400 hover:border-dark-500'
                      }`}
                    >
                      <LinkIcon className="w-5 h-5" />
                      <span className="text-xs">{t('madeForYou.youtubeUrlLabel')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setMfySourceType('upload')}
                      className={`flex flex-col items-center gap-1 p-3 rounded-lg border transition-all ${
                        mfySourceType === 'upload'
                          ? 'border-primary-500 bg-primary-500/10 text-primary-400'
                          : 'border-dark-700 text-dark-400 hover:border-dark-500'
                      }`}
                    >
                      <Upload className="w-5 h-5" />
                      <span className="text-xs">{t('madeForYou.fileUploadLabel')}</span>
                    </button>
                  </div>
                  <p className="mt-2 text-xs text-dark-500">
                    {mfySourceType === 'search' && t('madeForYou.autoSearchHint')}
                    {mfySourceType === 'youtube' && t('madeForYou.youtubeUrlHint')}
                    {mfySourceType === 'upload' && t('madeForYou.fileUploadHint')}
                  </p>
                </div>

                {/* YouTube URL (conditional) */}
                {mfySourceType === 'youtube' && (
                  <div>
                    <label htmlFor="mfy-youtube" className="block text-sm font-medium text-dark-300 mb-2">
                      {t('madeForYou.youtubeUrlFieldLabel')}
                    </label>
                    <input
                      type="url"
                      id="mfy-youtube"
                      value={mfyYoutubeUrl}
                      onChange={(e) => setMfyYoutubeUrl(e.target.value)}
                      placeholder={t('madeForYou.youtubeUrlPlaceholder')}
                      className="w-full px-4 py-3 bg-secondary border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent text-foreground placeholder:text-muted-foreground"
                    />
                  </div>
                )}

                {/* Email */}
                <div>
                  <label htmlFor="mfy-email" className="block text-sm font-medium text-dark-300 mb-2">
                    {t('madeForYou.emailLabel2')} <span className="text-red-400">{t('madeForYou.requiredField')}</span>
                  </label>
                  <input
                    type="email"
                    id="mfy-email"
                    value={mfyEmail}
                    onChange={(e) => setMfyEmail(e.target.value)}
                    placeholder={t('madeForYou.emailPlaceholder2')}
                    className="w-full px-4 py-3 bg-secondary border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent text-foreground placeholder:text-muted-foreground"
                  />
                  <p className="mt-1 text-xs text-dark-500">
                    {t('madeForYou.emailHint2')}
                  </p>
                </div>

                {/* Notes (optional) */}
                <div>
                  <label htmlFor="mfy-notes" className="block text-sm font-medium text-dark-300 mb-2">
                    {t('madeForYou.notesLabel')} <span className="text-dark-500">{t('madeForYou.notesOptional')}</span>
                  </label>
                  <textarea
                    id="mfy-notes"
                    value={mfyNotes}
                    onChange={(e) => setMfyNotes(e.target.value)}
                    rows={3}
                    placeholder={t('madeForYou.notesPlaceholder')}
                    className="w-full px-4 py-3 bg-secondary border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent text-foreground placeholder:text-muted-foreground resize-none"
                  />
                </div>

                {mfyError && <p className="text-red-400 text-sm">{mfyError}</p>}

                {/* Submit */}
                <button
                  type="submit"
                  disabled={mfyLoading}
                  className="w-full bg-primary-500 hover:bg-primary-600 disabled:bg-primary-500/50 text-white font-semibold py-4 rounded-xl transition-all btn-glow flex items-center justify-center gap-2"
                >
                  {mfyLoading ? (
                    <>
                      <Loader2 className="w-5 h-5 animate-spin" />
                      {t('madeForYou.processing')}
                    </>
                  ) : (
                    <>
                      <CreditCard className="w-5 h-5" />
                      {t('madeForYou.submitButton')}
                    </>
                  )}
                </button>

                <p className="text-xs text-center text-dark-500">
                  {t('madeForYou.paymentDisclaimer')}
                </p>
              </form>
            </div>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="py-16 px-4 bg-dark-800/50">
        <div className="max-w-2xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-12">{t('faq.title')}</h2>
          <div className="space-y-4">
            {faqs.map((faq, i) => (
              <div key={i} className="bg-dark-800 border border-dark-700 rounded-xl p-6">
                <h3 className="font-medium mb-2">{faq.question}</h3>
                <p className="text-dark-400 text-sm">{faq.answer}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-4 border-t border-primary-500/30">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" className="h-8" />
          </div>
          <div className="text-sm text-dark-500">
            {t('footer.copyright', { year: new Date().getFullYear() })}
          </div>
          <div className="flex gap-6 text-sm text-dark-400">
            <a href="mailto:support@nomadkaraoke.com" className="hover:text-primary-400 transition-colors">
              {t('footer.supportLink')}
            </a>
            <a href={`https://nomadkaraoke.com/${locale}/`} className="hover:text-primary-400 transition-colors">
              {t('footer.aboutLink')}
            </a>
          </div>
        </div>
      </footer>

      {/* Auth Dialog for sign-in */}
      <AuthDialog
        open={showAuthDialog}
        onClose={() => setShowAuthDialog(false)}
        onSuccess={handleAuthSuccess}
      />
    </div>
  );
}
