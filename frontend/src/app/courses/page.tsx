import Image from "next/image";
import Link from "next/link";

async function getCourses() {
  try {
    const res = await fetch(`http://127.0.0.1:8000/api/courses/`, { cache: 'no-store' });
    if (!res.ok) return [];
    return res.json();
  } catch (err) {
    console.error("Fetch error:", err);
    return [];
  }
}

export default async function CourseCatalog() {
  const courses = await getCourses();

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

      {/* Header */}
      <div className="pt-32 pb-12 px-6">
        <div className="max-w-7xl mx-auto">
          <h1 className="text-4xl md:text-5xl font-bold mb-4">Masterclasses</h1>
          <p className="text-zinc-400 text-lg">Browse our collection of premium Indian Classical Arts courses.</p>
        </div>
      </div>

      {/* Course Grid */}
      <div className="px-6">
        <div className="max-w-7xl mx-auto">
          {courses.length === 0 ? (
             <div className="text-center py-20 bg-zinc-900/30 border border-white/10 rounded-2xl">
               <h3 className="text-xl font-medium text-zinc-300">No courses available yet.</h3>
             </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
              {courses.map((course: any) => {
                const thumbnailUrl = course.thumbnail || "https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop";
                
                return (
                  <Link href={`/courses/${course.id}`} key={course.id} className="group flex flex-col bg-[#0a0a0a] border border-white/10 rounded-3xl overflow-hidden hover:border-[#facc15]/50 transition-colors shadow-2xl relative">
                    <div className="aspect-[4/3] bg-zinc-900 relative overflow-hidden flex items-center justify-center">
                       <img src={thumbnailUrl} alt={course.title} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-700" />
                    </div>
                    
                    <div className="p-6 flex flex-col flex-grow">
                      <div className="flex items-center gap-2 mb-3">
                        <span className="w-2 h-2 rounded-full bg-[#facc15]"></span>
                        <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Premium Course</span>
                      </div>
                      <h3 className="text-xl font-bold mb-2 group-hover:text-[#facc15] transition-colors">{course.title}</h3>
                      <p className="text-zinc-400 text-sm line-clamp-2 mb-6 flex-grow">{course.description}</p>
                      
                      <div className="flex items-center justify-between mt-auto">
                        <div className="flex items-center gap-2">
                           <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center text-xs font-bold border border-white/10">{course.title.charAt(0)}</div>
                           <span className="text-sm font-medium text-zinc-300">Instructor</span>
                        </div>
                        <span className="text-xl font-bold">₹{parseFloat(course.price).toLocaleString()}</span>
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
