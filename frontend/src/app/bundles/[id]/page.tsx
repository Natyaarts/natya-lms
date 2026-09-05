import Image from "next/image";
import Link from "next/link";
import BundleCheckoutButton from "@/components/BundleCheckoutButton";

export const dynamic = 'force-dynamic';

async function getBundle(id: string) {
  try {
    const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/orders/bundles/${id}/`, { cache: 'no-store' });
    if (!res.ok) return null;
    return res.json();
  } catch (err) {
    console.error("Fetch error in getBundle:", err);
    return null;
  }
}

export default async function BundleDetail(props: { params: Promise<{ id: string }> }) {
  const params = await props.params;
  const bundle = await getBundle(params.id);

  if (!bundle) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center text-white">
        <h1 className="text-2xl">Bundle not found</h1>
      </div>
    );
  }

  const thumbnailUrl = bundle.thumbnail || "https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop";

  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-[#facc15] selection:text-black pb-24">
      <nav className="border-b border-white/10 bg-black/50 backdrop-blur-md fixed top-0 w-full z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <Link href="/" className="flex items-center">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain" />
          </Link>
          <div className="flex gap-4">
            <Link href="/bundles" className="text-sm font-medium hover:text-[#facc15] transition-colors">All Bundles</Link>
            <Link href="/dashboard" className="text-sm font-medium hover:text-[#facc15] transition-colors">Dashboard</Link>
          </div>
        </div>
      </nav>

      <div className="relative pt-32 pb-20 px-6 overflow-hidden">
        <div className="absolute top-1/4 right-0 w-[500px] h-[500px] bg-[#facc15]/10 rounded-full blur-[120px] pointer-events-none -z-10" />

        <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-16 items-start">
          <div>
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/5 border border-white/10 text-sm font-medium mb-6">
              <span className="w-2 h-2 rounded-full bg-[#facc15]"></span>
              Bundle &middot; {bundle.courses?.length || 0} Courses
            </div>

            <h1 className="text-5xl md:text-6xl font-bold leading-tight mb-6">{bundle.name}</h1>
            <p className="text-xl text-zinc-400 mb-10 leading-relaxed whitespace-pre-line">{bundle.description}</p>

            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wide mb-4">Included Courses</h2>
            <div className="space-y-3">
              {(bundle.courses || []).map((course: any) => (
                <div key={course.id} className="flex items-center gap-4 bg-[#0a0a0a] border border-white/10 rounded-2xl p-4">
                  <div className="w-14 h-14 rounded-xl overflow-hidden bg-zinc-900 shrink-0 flex items-center justify-center">
                    <img src={course.thumbnail || thumbnailUrl} alt={course.title} className="w-full h-full object-cover" />
                  </div>
                  <div className="flex-1">
                    <div className="font-semibold">{course.title}</div>
                    {!course.is_published && <div className="text-xs text-zinc-600">Not yet published</div>}
                  </div>
                  <div className="text-sm text-zinc-500">₹{parseFloat(course.price).toLocaleString()}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="relative lg:sticky lg:top-32">
            <div className="absolute inset-0 bg-gradient-to-tr from-[#facc15]/20 to-transparent blur-3xl -z-10 rounded-3xl" />
            <div className="bg-[#0a0a0a] border border-white/10 rounded-[2rem] p-8 shadow-2xl relative overflow-hidden">
              <div className="relative w-full h-48 rounded-xl overflow-hidden mb-8 bg-zinc-900 flex items-center justify-center">
                <img src={thumbnailUrl} alt={bundle.name} className="w-full h-full object-cover" />
              </div>

              <div className="flex items-end gap-2 mb-2">
                <span className="text-5xl font-bold tracking-tight">₹{parseFloat(bundle.price).toLocaleString()}</span>
              </div>
              <p className="text-sm text-[#facc15] font-medium mb-8">One payment, {bundle.courses?.length || 0} masterclasses</p>

              <div className="space-y-4 mb-8">
                <div className="flex items-center gap-3 text-zinc-300">
                  <svg className="w-5 h-5 text-[#facc15]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                  Full lifetime access to every course
                </div>
                <div className="flex items-center gap-3 text-zinc-300">
                  <svg className="w-5 h-5 text-[#facc15]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                  Instant access to every course in the bundle
                </div>
              </div>

              <BundleCheckoutButton bundleId={bundle.id} price={bundle.price} isPurchasable={bundle.is_purchasable} />

              <div className="mt-4 flex justify-center items-center gap-2 text-xs text-zinc-500 font-medium">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" /></svg>
                Secure payments by Razorpay
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
