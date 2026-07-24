import Image from "next/image";
import CheckoutButton from "@/components/CheckoutButton";
import Link from "next/link";

async function getCourse(id: string) {
  try {
    const res = await fetch(`http://127.0.0.1:8000/api/courses/${id}/`, { cache: 'no-store' });
    if (!res.ok) {
      console.error("API response not ok", res.status);
      return null;
    }
    return res.json();
  } catch (err) {
    console.error("Fetch error in getCourse:", err);
    return null;
  }
}

export default async function CourseDetail(props: { params: Promise<{ id: string }> }) {
  const params = await props.params;
  const course = await getCourse(params.id);

  if (!course) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center text-white">
        <h1 className="text-2xl">Course not found</h1>
      </div>
    );
  }

  // Fallback images if none provided
  const thumbnailUrl = course.thumbnail || "https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop";

  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-[#facc15] selection:text-black pb-24">
      {/* Navigation Bar */}
      <nav className="border-b border-white/10 bg-black/50 backdrop-blur-md fixed top-0 w-full z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <Link href="/" className="flex items-center">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain" />
          </Link>
          <div className="flex gap-4">
            <Link href="/dashboard" className="text-sm font-medium hover:text-[#facc15] transition-colors">Dashboard</Link>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <div className="relative pt-32 pb-20 px-6 overflow-hidden">
        <div className="absolute top-1/4 right-0 w-[500px] h-[500px] bg-[#facc15]/10 rounded-full blur-[120px] pointer-events-none -z-10" />
        
        <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
          <div>
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/5 border border-white/10 text-sm font-medium mb-6">
              <span className="w-2 h-2 rounded-full bg-[#facc15]"></span>
              Premium Masterclass
            </div>
            
            <h1 className="text-5xl md:text-6xl font-bold leading-tight mb-6">
              {course.title}
            </h1>
            
            <p className="text-xl text-zinc-400 mb-8 leading-relaxed whitespace-pre-line">
              {course.description}
            </p>

            <div className="flex items-center gap-4 mb-8">
              <div className="w-12 h-12 rounded-full bg-zinc-800 border-2 border-[#facc15] overflow-hidden relative flex items-center justify-center font-bold text-xl">
                 {course.title.charAt(0)}
              </div>
              <div>
                <div className="text-sm text-zinc-500">Taught by</div>
                <div className="font-semibold">Instructor</div>
              </div>
            </div>

          </div>

          {/* Checkout Card */}
          <div className="relative">
            <div className="absolute inset-0 bg-gradient-to-tr from-[#facc15]/20 to-transparent blur-3xl -z-10 rounded-3xl" />
            <div className="bg-[#0a0a0a] border border-white/10 rounded-[2rem] p-8 shadow-2xl relative overflow-hidden">
              <div className="absolute top-0 right-0 p-8 opacity-10">
                <svg width="120" height="120" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14.5v-9l6 4.5-6 4.5z"/>
                </svg>
              </div>

              <div className="relative w-full h-48 rounded-xl overflow-hidden mb-8 bg-zinc-900 flex items-center justify-center">
                <img src={thumbnailUrl} alt={course.title} className="w-full h-full object-cover" />
              </div>

              <div className="flex items-end gap-2 mb-2">
                <span className="text-5xl font-bold tracking-tight">₹{parseFloat(course.price).toLocaleString()}</span>
              </div>
              <p className="text-sm text-[#facc15] font-medium mb-8">Special early bird offer</p>

              <div className="space-y-4 mb-8">
                <div className="flex items-center gap-3 text-zinc-300">
                  <svg className="w-5 h-5 text-[#facc15]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                  Full lifetime access
                </div>
                <div className="flex items-center gap-3 text-zinc-300">
                  <svg className="w-5 h-5 text-[#facc15]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                  Access on mobile and web
                </div>
                <div className="flex items-center gap-3 text-zinc-300">
                  <svg className="w-5 h-5 text-[#facc15]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                  Certificate of completion
                </div>
              </div>

              <CheckoutButton courseId={course.id} price={course.price} />
              
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
