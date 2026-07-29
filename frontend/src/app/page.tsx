"use client";

import Link from "next/link";
import Image from "next/image";
import { motion, useScroll, useTransform, useMotionValue, useSpring, useMotionTemplate } from "framer-motion";
import { useState, useEffect, MouseEvent, useRef } from "react";

// --- Advanced 3D Tilt Card Component ---
function TiltCard({ children, className }: { children: React.ReactNode, className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  
  const x = useMotionValue(0);
  const y = useMotionValue(0);

  const mouseXSpring = useSpring(x, { stiffness: 300, damping: 30 });
  const mouseYSpring = useSpring(y, { stiffness: 300, damping: 30 });

  const rotateX = useTransform(mouseYSpring, [-0.5, 0.5], ["15deg", "-15deg"]);
  const rotateY = useTransform(mouseXSpring, [-0.5, 0.5], ["-15deg", "15deg"]);

  const handleMouseMove = (e: MouseEvent<HTMLDivElement>) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    
    const width = rect.width;
    const height = rect.height;
    
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    
    const xPct = mouseX / width - 0.5;
    const yPct = mouseY / height - 0.5;
    
    x.set(xPct);
    y.set(yPct);
  };

  const handleMouseLeave = () => {
    x.set(0);
    y.set(0);
  };

  return (
    <motion.div
      ref={ref}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{
        rotateY,
        rotateX,
        transformStyle: "preserve-3d",
        willChange: "transform",
      }}
      className={`relative ${className}`}
    >
      <div
        style={{
          transform: "translateZ(50px)",
          transformStyle: "preserve-3d",
        }}
        className="w-full h-full"
      >
        {children}
      </div>
    </motion.div>
  );
}

export default function Home() {
  const [content, setContent] = useState<any>(null);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  
  const { scrollY } = useScroll();
  const y1 = useTransform(scrollY, [0, 1000], [0, 400]);
  const opacityHero = useTransform(scrollY, [0, 500], [1, 0]);
  const scaleHero = useTransform(scrollY, [0, 500], [1, 0.8]);

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/cms/landing-page/`)
      .then(res => res.json())
      .then(data => setContent(data))
      .catch(err => console.error("Error fetching CMS content", err));

    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/auth/user/`, { credentials: 'include' })
      .then(res => {
        if (res.ok) setIsLoggedIn(true);
      })
      .catch(err => console.error("Auth check failed", err));
  }, []);

  const hero = content?.hero || {
    title: "Mastering Indian Classical Arts.",
    subtitle: "Welcome to Natya LMS",
    description: "Premium pre-recorded masterclasses, multi-lingual AI dubbing, and structured learning for all.",
    button_text: "Browse Masterclasses",
    button_link: "/courses",
    bg_image_url: "https://natyaarts.com/img/hero.png"
  };

  const features = content?.features?.length > 0 ? content.features : [
    {
      title: "Flawless Dubbing",
      description: "Our AI retains the emotional cadence and tone of the original teacher, generating high-fidelity audio.",
      icon_name: "mic"
    },
    {
      title: "Seamless Switch",
      description: "Change languages instantly mid-video right from our custom built player interface.",
      icon_name: "globe"
    },
    {
      title: "Cultural Integrity",
      description: "Translations are context-aware, ensuring classical terms like Mudras remain perfectly authentic.",
      icon_name: "heart"
    }
  ];

  return (
    <div className="min-h-screen flex flex-col bg-black text-white font-sans selection:bg-[#facc15] selection:text-black overflow-x-hidden">
      
      {/* Animated Navbar */}
      <motion.nav 
        initial={{ y: -100, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 1, ease: "easeOut", delay: 0.5 }}
        className="fixed top-0 left-0 right-0 z-50 flex justify-center pt-6 px-6 md:px-12 bg-gradient-to-b from-black/90 via-black/50 to-transparent pb-8 pointer-events-none"
      >
        <div className="flex justify-between items-center w-full max-w-7xl pointer-events-auto">
          <Link href="/" className="flex items-center group relative z-50 overflow-visible hover:scale-105 transition-transform duration-500">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain drop-shadow-[0_0_15px_rgba(250,204,21,0.3)]" />
          </Link>
          
          <div className="flex items-center gap-4 z-50">
            {isLoggedIn ? (
              <Link href="/dashboard" className="relative group px-8 py-3 overflow-hidden rounded-full bg-[#facc15] text-black text-sm font-bold uppercase tracking-[0.2em] transition-all hover:scale-105 hover:shadow-[0_0_40px_rgba(250,204,21,0.6)]">
                <span className="relative z-10">Dashboard</span>
              </Link>
            ) : (
              <>
                <Link href="/login" className="hidden md:flex items-center gap-2 px-5 py-2 text-white/70 text-sm font-bold uppercase tracking-widest rounded-full hover:text-white hover:bg-white/10 transition-all duration-300">
                  Sign In
                </Link>
                <Link href="/register" className="relative group px-8 py-3 overflow-hidden rounded-full bg-white text-black text-sm font-bold uppercase tracking-[0.2em] transition-all hover:scale-105 hover:shadow-[0_0_40px_rgba(255,255,255,0.4)]">
                  <span className="relative z-10">Sign Up</span>
                  <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/50 to-transparent -translate-x-full group-hover:animate-[shimmer_1.5s_infinite]" />
                </Link>
              </>
            )}
          </div>
        </div>
      </motion.nav>

      <main className="flex-grow flex flex-col">
        {/* Extreme Parallax Hero Section */}
        <section className="relative min-h-[100vh] bg-black overflow-hidden flex items-center justify-center pt-20">
          
          {/* Parallax Background */}
          <motion.div 
            style={{ y: y1 }}
            className="absolute inset-0 w-full h-[120%] -top-[10%] z-0"
          >
            <div className="absolute inset-0 bg-gradient-to-b from-black/40 via-black/60 to-black z-10" />
            <img 
              src={hero.bg_image_url} 
              alt="Hero Background" 
              className="w-full h-full object-cover object-top opacity-50 scale-105"
            />
          </motion.div>

          <motion.div 
            style={{ opacity: opacityHero, scale: scaleHero }}
            className="relative z-10 container mx-auto px-6 text-center flex flex-col items-center mt-20"
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.5, filter: "blur(20px)" }}
              animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
              transition={{ duration: 1.5, ease: [0.16, 1, 0.3, 1] }}
              className="inline-block mb-6 px-6 py-2 rounded-full border border-white/10 bg-white/5 backdrop-blur-xl shadow-[0_0_30px_rgba(255,255,255,0.05)]"
            >
              <span className="text-zinc-300 font-bold tracking-[0.3em] uppercase text-xs md:text-sm">
                {hero.subtitle}
              </span>
            </motion.div>
            
            <h1 className="text-5xl md:text-7xl lg:text-[6rem] font-bold tracking-tighter text-white leading-[1.05] mb-8 max-w-5xl mix-blend-lighten">
              {hero.title.split(' ').map((word: string, i: number) => (
                <motion.span
                  key={i}
                  initial={{ opacity: 0, y: 50, rotateX: -90 }}
                  animate={{ opacity: 1, y: 0, rotateX: 0 }}
                  transition={{ duration: 1, delay: i * 0.15, ease: [0.2, 0.8, 0.2, 1] }}
                  className="inline-block mr-4 [perspective:1000px]"
                >
                  {word.includes('Classical') || word.includes('Arts.') ? (
                    <span className="text-transparent bg-clip-text bg-gradient-to-br from-[#facc15] via-[#fbbf24] to-[#a16207]">
                      {word}
                    </span>
                  ) : word}
                </motion.span>
              ))}
            </h1>
            
            <motion.p 
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 1, delay: 1 }}
              className="text-lg md:text-2xl text-zinc-400 max-w-3xl font-medium tracking-tight mb-16 leading-relaxed"
            >
              {hero.description}
            </motion.p>
            
            <motion.div 
              initial={{ opacity: 0, y: 40 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 1, delay: 1.2, type: "spring" }}
            >
              <Link href={hero.button_link} className="group relative inline-flex items-center justify-center px-10 py-5 font-bold text-black transition-all duration-300 bg-[#facc15] rounded-full hover:scale-110 hover:shadow-[0_0_60px_rgba(250,204,21,0.6)] focus:outline-none overflow-hidden">
                <div className="absolute inset-0 w-full h-full -mt-1 rounded-lg opacity-30 bg-gradient-to-b from-transparent via-transparent to-black" />
                <span className="relative flex items-center gap-3 text-lg tracking-wider uppercase">
                  {hero.button_text}
                  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="transition-transform duration-500 group-hover:translate-x-2"><path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path></svg>
                </span>
                <div className="absolute inset-0 border-2 border-white/20 rounded-full group-hover:scale-105 transition-transform duration-500" />
              </Link>
            </motion.div>
          </motion.div>
          
          {/* Scroll Indicator */}
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 2, duration: 1 }}
            className="absolute bottom-10 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2 z-20"
          >
            <span className="text-zinc-500 text-xs font-bold tracking-widest uppercase">Scroll</span>
            <motion.div 
              animate={{ y: [0, 10, 0] }} 
              transition={{ repeat: Infinity, duration: 2, ease: "easeInOut" }}
              className="w-[2px] h-12 bg-gradient-to-b from-white/50 to-transparent rounded-full"
            />
          </motion.div>
        </section>

        {/* 3D Features Section */}
        <section className="py-40 px-6 max-w-7xl mx-auto relative z-20">
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full pointer-events-none -z-10" style={{ background: 'radial-gradient(circle, rgba(250,204,21,0.05) 0%, transparent 70%)' }} />
          
          <div className="text-center mb-32">
            <motion.div
              initial={{ opacity: 0, scale: 0.8 }}
              whileInView={{ opacity: 1, scale: 1 }}
              viewport={{ once: true, margin: "-100px" }}
              transition={{ duration: 0.8 }}
              className="inline-block mb-6 px-6 py-2 rounded-full border border-white/10 bg-white/5 backdrop-blur-md"
            >
              <h2 className="text-sm font-bold tracking-[0.3em] uppercase text-transparent bg-clip-text bg-gradient-to-r from-zinc-400 to-zinc-200">
                Groundbreaking Technology
              </h2>
            </motion.div>
            
            <motion.h3 
              initial={{ opacity: 0, y: 50, filter: "blur(10px)" }}
              whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              viewport={{ once: true, margin: "-100px" }}
              transition={{ duration: 1, delay: 0.2 }}
              className="text-5xl md:text-7xl font-bold tracking-tighter text-white"
            >
              Learn in your language.
              <br />
              <span className="text-zinc-600 inline-block mt-4">Without losing the art.</span>
            </motion.h3>
          </div>
          
          <div className="grid lg:grid-cols-2 gap-10 lg:gap-16 items-center [perspective:2000px]">
            
            <TiltCard className="h-full w-full">
              <motion.div 
                initial={{ opacity: 0, rotateY: -30, x: -100 }}
                whileInView={{ opacity: 1, rotateY: 0, x: 0 }}
                viewport={{ once: true, margin: "-100px" }}
                transition={{ duration: 1.2, ease: "easeOut" }}
                className="relative overflow-hidden bg-gradient-to-br from-[#1a1a1a] to-[#0a0a0a] p-3 rounded-[2.5rem] border border-white/10 shadow-[0_30px_60px_rgba(0,0,0,0.8)] group h-full"
              >
                <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                <div className="relative rounded-[2rem] overflow-hidden aspect-[4/3] bg-black border border-white/5">
                  <motion.video 
                    whileHover={{ scale: 1.05 }}
                    transition={{ duration: 0.8 }}
                    src="/video.mp4" 
                    autoPlay
                    loop
                    muted
                    playsInline
                    className="w-full h-full object-cover opacity-70" 
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-black via-black/40 to-transparent flex flex-col justify-end p-8">
                    <div className="flex justify-between items-end translate-z-[60px]">
                      <div>
                        <h3 className="text-3xl font-bold text-white mb-2 tracking-tight drop-shadow-lg">Adavu Fundamentals</h3>
                        <p className="text-[#facc15] font-semibold tracking-wide text-sm drop-shadow-md">By Kalamandalam Sivaprasad</p>
                      </div>
                      <div className="flex bg-black/80 backdrop-blur-xl p-1.5 rounded-full border border-white/20 shadow-2xl">
                        <div className="px-5 py-2 text-xs font-bold tracking-widest text-zinc-400 rounded-full hover:bg-white/10 hover:text-white cursor-pointer transition-all">EN</div>
                        <div className="px-5 py-2 text-xs font-bold tracking-widest text-black bg-white rounded-full cursor-pointer shadow-[0_0_20px_rgba(255,255,255,0.4)]">ML</div>
                      </div>
                    </div>
                  </div>
                  <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-20 h-20 bg-white/10 backdrop-blur-xl border border-white/30 rounded-full flex items-center justify-center cursor-pointer group-hover:scale-110 group-hover:bg-white/20 transition-all duration-500 shadow-[0_0_40px_rgba(0,0,0,0.5)]">
                    <svg xmlns="http://www.w3.org/2000/svg" width="30" height="30" viewBox="0 0 24 24" fill="white" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="ml-2 drop-shadow-lg"><path d="M5 3a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"></path></svg>
                  </div>
                </div>
              </motion.div>
            </TiltCard>
            
            <TiltCard className="h-full w-full">
              <motion.div 
                initial={{ opacity: 0, rotateY: 30, x: 100 }}
                whileInView={{ opacity: 1, rotateY: 0, x: 0 }}
                viewport={{ once: true, margin: "-100px" }}
                transition={{ duration: 1.2, ease: "easeOut" }}
                className="relative overflow-hidden bg-gradient-to-br from-[#1a1a1a] to-[#0a0a0a] p-10 md:p-14 rounded-[2.5rem] border border-white/10 shadow-[0_30px_60px_rgba(0,0,0,0.8)] h-full flex flex-col justify-center group"
              >
                <div className="absolute -bottom-40 -right-40 w-[500px] h-[500px] rounded-full group-hover:scale-110 transition-all duration-1000" style={{ background: 'radial-gradient(circle, rgba(250,204,21,0.1) 0%, transparent 70%)' }} />
                <div className="absolute -top-40 -left-40 w-[400px] h-[400px] rounded-full transition-all duration-1000" style={{ background: 'radial-gradient(circle, rgba(255,255,255,0.05) 0%, transparent 70%)' }} />
                
                <div className="relative z-10 space-y-12 translate-z-[50px]">
                  {features.map((feature: any, i: number) => (
                    <motion.div 
                      key={i}
                      initial={{ opacity: 0, y: 30 }}
                      whileInView={{ opacity: 1, y: 0 }}
                      viewport={{ once: true, margin: "-50px" }}
                      transition={{ duration: 0.8, delay: i * 0.2 + 0.5 }}
                      className="group/item hover:-translate-y-2 transition-transform duration-300"
                    >
                      <div className="flex items-start gap-6">
                        <div className="w-16 h-16 shrink-0 bg-gradient-to-br from-white/10 to-transparent border border-white/10 rounded-2xl flex items-center justify-center backdrop-blur-md shadow-xl group-hover/item:border-[#facc15]/50 group-hover/item:shadow-[0_0_30px_rgba(250,204,21,0.2)] transition-all duration-500">
                          {feature.icon_name === 'mic' && <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[#facc15]"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" x2="12" y1="19" y2="22"></line></svg>}
                          {feature.icon_name === 'globe' && <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[#facc15]"><circle cx="12" cy="12" r="10"></circle><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path><path d="M2 12h20"></path></svg>}
                          {feature.icon_name === 'heart' && <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[#facc15]"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"></path></svg>}
                        </div>
                        <div>
                          <h4 className="text-2xl font-bold text-white mb-3 tracking-tight">{feature.title}</h4>
                          <p className="text-zinc-400 font-medium leading-relaxed text-lg">{feature.description}</p>
                        </div>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </motion.div>
            </TiltCard>
          </div>
        </section>

        {/* Extreme Call to Action */}
        <section className="py-52 px-6 text-center border-t border-white/5 relative overflow-hidden bg-[#050505]">
          <motion.div 
            animate={{ 
              scale: [1, 1.2, 1],
              opacity: [0.3, 0.5, 0.3],
            }}
            transition={{ duration: 10, repeat: Infinity, ease: "easeInOut" }}
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[1000px] h-[1000px] rounded-full pointer-events-none" 
            style={{ background: 'radial-gradient(circle, rgba(250,204,21,0.1) 0%, transparent 70%)' }}
          />
          <div className="absolute inset-0 bg-[url('https://natyaarts.com/img/noise.png')] opacity-20 mix-blend-overlay pointer-events-none" />
          
          <div className="relative z-10 max-w-4xl mx-auto flex flex-col items-center">
            <motion.div
              initial={{ opacity: 0, scale: 0.5, filter: "blur(20px)" }}
              whileInView={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
              viewport={{ once: true, margin: "-100px" }}
              transition={{ duration: 1.5, ease: "easeOut" }}
            >
              <h2 className="text-6xl md:text-8xl lg:text-[7rem] font-bold tracking-tighter text-white mb-8 leading-tight">
                Start learning <br/><span className="text-transparent bg-clip-text bg-gradient-to-r from-white to-zinc-600">today.</span>
              </h2>
            </motion.div>
            
            <motion.p 
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-100px" }}
              transition={{ duration: 1, delay: 0.3 }}
              className="text-2xl md:text-3xl text-zinc-400 font-medium mb-16 tracking-tight"
            >
              Embrace the heritage. Master the art.
            </motion.p>
            
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-100px" }}
              transition={{ duration: 1, delay: 0.5 }}
            >
              <Link href={hero.button_link} className="group relative inline-flex items-center justify-center px-12 py-6 font-bold text-black transition-all duration-500 bg-white rounded-full hover:scale-110 hover:shadow-[0_0_80px_rgba(255,255,255,0.4)] focus:outline-none overflow-hidden">
                <div className="absolute inset-0 w-full h-full bg-gradient-to-r from-transparent via-black/10 to-transparent -translate-x-full group-hover:animate-[shimmer_1s_infinite]" />
                <span className="relative text-xl tracking-[0.2em] uppercase">
                  {hero.button_text}
                </span>
              </Link>
            </motion.div>
          </div>
        </section>
      </main>
      
      <footer className="bg-[#020202] border-t border-white/5 py-12 text-center relative z-20">
        <p className="text-zinc-600 text-sm font-medium tracking-wider uppercase">© 2026 Natya LMS. All rights reserved.</p>
      </footer>
      
      <style dangerouslySetInnerHTML={{__html: `
        @keyframes shimmer {
          100% { transform: translateX(100%); }
        }
      `}} />
    </div>
  );
}
