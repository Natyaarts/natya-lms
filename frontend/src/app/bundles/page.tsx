import Image from "next/image";
import Link from "next/link";

export const dynamic = 'force-dynamic';

// Phase 3.3: mirrors frontend/src/app/courses/page.tsx exactly -- same
// server-component fetch pattern (no cookies forwarded), same visual
// system -- for the new Bundle catalog. /api/orders/bundles/ is public-read
// (IsSuperAdminOrAdminOrReadOnly), matching CourseViewSet's public-read
// posture for published courses, specifically so this page works the same
// way courses/page.tsx does.
async function getBundles() {
  try {
    const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/orders/bundles/`, { cache: 'no-store' });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data) ? data : data.results || [];
  } catch (err) {
    console.error("Fetch error:", err);
    return [];
  }
}

export default async function BundleCatalog() {
  const bundles = await getBundles();

  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-[#facc15] selection:text-black pb-24">
      <nav className="border-b border-white/10 bg-black/50 backdrop-blur-md fixed top-0 w-full z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <Link href="/" className="flex items-center">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain" />
          </Link>
          <div className="flex gap-4">
            <Link href="/courses" className="text-sm font-medium hover:text-[#facc15] transition-colors">Courses</Link>
            <Link href="/dashboard" className="text-sm font-medium hover:text-[#facc15] transition-colors">Dashboard</Link>
          </div>
        </div>
      </nav>

      <div className="pt-32 pb-12 px-6">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-4xl md:text-5xl font-bold mb-4">Course Bundles</h1>
          <p className="text-zinc-400 text-lg">Multiple masterclasses, one purchase.</p>
        </div>
      </div>

      <div className="px-6">
        <div className="max-w-7xl mx-auto">
          {bundles.length === 0 ? (
            <div className="text-center py-20 bg-zinc-900/30 border border-white/10 rounded-2xl">
              <h3 className="text-xl font-medium text-zinc-300">No bundles available yet.</h3>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
              {bundles.map((bundle: any) => {
                const thumbnailUrl = bundle.thumbnail || "https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop";
                return (
                  <Link href={`/bundles/${bundle.id}`} key={bundle.id} className="group flex flex-col bg-[#0a0a0a] border border-white/10 rounded-3xl overflow-hidden hover:border-[#facc15]/50 transition-colors shadow-2xl relative">
                    <div className="aspect-[4/3] bg-zinc-900 relative overflow-hidden flex items-center justify-center">
                      <img src={thumbnailUrl} alt={bundle.name} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-700" />
                    </div>
                    <div className="p-6 flex flex-col flex-grow">
                      <div className="flex items-center gap-2 mb-3">
                        <span className="w-2 h-2 rounded-full bg-[#facc15]"></span>
                        <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                          Bundle &middot; {bundle.courses?.length || 0} Courses
                        </span>
                        {!bundle.is_purchasable && (
                          <span className="text-xs font-medium text-zinc-600 uppercase tracking-wider ml-auto">Coming Soon</span>
                        )}
                      </div>
                      <h3 className="text-xl font-bold mb-2 group-hover:text-[#facc15] transition-colors">{bundle.name}</h3>
                      <p className="text-zinc-400 text-sm line-clamp-2 mb-6 flex-grow">{bundle.description}</p>
                      <div className="flex items-center justify-between mt-auto">
                        <span className="text-sm text-zinc-500">
                          {(bundle.courses || []).slice(0, 3).map((c: any) => c.title).join(", ")}
                        </span>
                        <span className="text-xl font-bold shrink-0 ml-3">₹{parseFloat(bundle.price).toLocaleString()}</span>
                      </div>
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
