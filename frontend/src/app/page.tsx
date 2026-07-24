"use client";

import Link from "next/link";
import Image from "next/image";
import { motion } from "framer-motion";
import { useState, useEffect } from "react";

export default function Home() {
  const [content, setContent] = useState<any>(null);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    fetch("http://localhost:8000/api/cms/landing-page/")
      .then(res => res.json())
      .then(data => setContent(data))
      .catch(err => console.error("Error fetching CMS content", err));

    fetch("http://localhost:8000/api/auth/user/", { credentials: 'include' })
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
    <div className="min-h-screen flex flex-col bg-black text-white font-sans selection:bg-[#facc15] selection:text-black">
      
      {/* Navbar */}
      <nav className="fixed top-0 left-0 right-0 z-50 flex justify-center pt-6 px-6 md:px-12 bg-gradient-to-b from-black/80 to-transparent pb-4 pointer-events-none">
        <div className="flex justify-between items-center w-full max-w-7xl pointer-events-auto">
          <Link href="/" className="flex items-center group relative z-50 overflow-visible">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain" />
          </Link>
          
          <div className="flex items-center gap-3 z-50">
            {isLoggedIn ? (
              <Link href="/dashboard" className="hidden md:flex items-center gap-2 px-6 py-2 bg-[#facc15] text-black text-sm font-medium uppercase tracking-wider rounded-full hover:bg-yellow-400 transition-all duration-300 shadow-lg">
                Dashboard
              </Link>
            ) : (
              <>
                <Link href="/login" className="hidden md:flex items-center gap-2 px-5 py-2 text-white text-sm font-medium uppercase tracking-wider rounded-full hover:bg-white/10 transition-all duration-300">
                  Sign In
                </Link>
                <Link href="/register" className="hidden md:flex items-center gap-2 px-6 py-2 bg-white text-black text-sm font-medium uppercase tracking-wider rounded-full hover:bg-zinc-200 transition-all duration-300 shadow-lg">
                  Sign Up
                </Link>
              </>
            )}
          </div>
        </div>
      </nav>

      <main className="flex-grow flex flex-col">
        {/* Hero Section */}
        <section className="relative min-h-[100vh] bg-black overflow-hidden pt-40">
          <div className="relative z-10 container mx-auto px-6 text-center flex flex-col items-center">
            <motion.h2 
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8 }}
              className="text-zinc-400 font-semibold tracking-widest uppercase text-sm md:text-base mb-6"
            >
              {hero.subtitle}
            </motion.h2>
            
            <motion.h1 
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.8, delay: 0.1 }}
              className="text-4xl md:text-6xl lg:text-7xl font-semibold tracking-tight text-white leading-[1.1] mb-8"
              dangerouslySetInnerHTML={{
                __html: hero.title.replace('Classical Arts.', '<span class="text-transparent bg-clip-text bg-gradient-to-br from-[#facc15] to-[#a16207]">Classical Arts.</span>')
              }}
            />
            
            <motion.p 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.8, delay: 0.2 }}
              className="text-xl md:text-3xl text-zinc-400 max-w-3xl font-medium tracking-tight mb-12"
            >
              {hero.description}
            </motion.p>
            
            <motion.div 
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.3 }}
              className="flex flex-col sm:flex-row items-center gap-4"
            >
              <Link href={hero.button_link} className="px-8 py-4 bg-white text-black font-semibold rounded-full hover:bg-zinc-200 transition-colors text-base flex items-center gap-2">
                {hero.button_text}
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"></path></svg>
              </Link>
            </motion.div>
          </div>
          
          <div className="absolute bottom-0 left-0 right-0 h-[60vh] md:h-[70vh] w-full z-0 overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-b from-black via-black/60 to-transparent z-10" />
            <div className="absolute inset-x-0 bottom-0 h-64 bg-gradient-to-t from-black to-transparent z-10" />
            <img 
              src={hero.bg_image_url} 
              alt="Hero Background" 
              className="w-full h-full object-cover object-top opacity-70"
            />
          </div>
        </section>

        {/* Features Section */}
        <section className="py-32 px-6 max-w-7xl mx-auto">
          <div className="text-center mb-24">
            <motion.h2 
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="text-sm font-semibold tracking-widest uppercase text-zinc-500 mb-4"
            >
              Groundbreaking Technology
            </motion.h2>
            <motion.p 
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="text-4xl md:text-6xl font-medium tracking-tight text-white"
            >
              Learn in your language.
            </motion.p>
            <motion.p 
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="text-4xl md:text-6xl font-medium tracking-tight text-zinc-600 mt-2"
            >
              Without losing the art.
            </motion.p>
          </div>
          
          <div className="grid md:grid-cols-2 gap-6 md:gap-10">
            <motion.div 
              initial={{ opacity: 0, y: 50 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="relative overflow-hidden bg-gradient-to-br from-[#151515] to-[#0a0a0a] p-2 rounded-[2rem] border border-white/10 shadow-2xl group"
            >
              <div className="relative rounded-[1.5rem] overflow-hidden aspect-[4/3] bg-zinc-900 border border-white/5">
                <img src="https://natyaarts.com/img/hero2.png" alt="Dance Video" className="w-full h-full object-cover opacity-80" />
                <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent flex flex-col justify-end p-8">
                  <div className="flex justify-between items-end">
                    <div>
                      <h3 className="text-2xl font-semibold text-white mb-1 tracking-tight">Adavu Fundamentals</h3>
                      <p className="text-zinc-400 font-medium text-sm">By Kalamandalam Sivaprasad</p>
                    </div>
                    <div className="flex bg-black/60 backdrop-blur-md p-1 rounded-full border border-white/10">
                      <div className="px-4 py-1.5 text-xs font-semibold text-zinc-400 rounded-full hover:bg-white/10 cursor-pointer">EN</div>
                      <div className="px-4 py-1.5 text-xs font-semibold text-black bg-white rounded-full cursor-pointer">ML</div>
                    </div>
                  </div>
                </div>
                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-16 h-16 bg-white/10 backdrop-blur-md border border-white/20 rounded-full flex items-center justify-center cursor-pointer hover:scale-105 transition-transform">
                  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="white" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="ml-1"><path d="M5 3a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"></path></svg>
                </div>
              </div>
            </motion.div>
            
            <motion.div 
              initial={{ opacity: 0, y: 50 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="relative overflow-hidden bg-gradient-to-br from-[#151515] to-[#0a0a0a] p-8 md:p-12 rounded-[2rem] border border-white/10 flex flex-col justify-between shadow-2xl group"
            >
              <div className="absolute -bottom-32 -right-32 w-96 h-96 bg-[#facc15]/10 rounded-full blur-[120px] group-hover:bg-[#facc15]/20 transition-colors duration-1000" />
              <div className="relative z-10">
                <div className="w-14 h-14 bg-white/5 border border-white/10 rounded-full flex items-center justify-center mb-10 backdrop-blur-md">
                  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-200"><path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"></path><circle cx="12" cy="12" r="3"></circle></svg>
                </div>
                
                <div className="space-y-8">
                  {features.map((feature: any, i: number) => (
                    <div key={i}>
                      <h4 className="text-xl font-semibold text-white mb-2">{feature.title}</h4>
                      <p className="text-zinc-400 font-medium leading-relaxed">{feature.description}</p>
                    </div>
                  ))}
                </div>
              </div>
            </motion.div>
          </div>
        </section>

        {/* Call to Action */}
        <section className="py-40 px-6 text-center border-t border-white/10 relative overflow-hidden">
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-[#facc15]/5 rounded-full blur-[150px] pointer-events-none" />
          <div className="relative z-10">
            <h2 className="text-5xl md:text-7xl font-semibold tracking-tighter text-white mb-8">Start learning today.</h2>
            <p className="text-2xl text-zinc-400 font-medium mb-12">Embrace the heritage. Master the art.</p>
            <Link href={hero.button_link} className="inline-block px-10 py-5 bg-white text-black font-semibold text-xl rounded-full hover:scale-105 transition-transform shadow-xl">
              {hero.button_text}
            </Link>
          </div>
        </section>
      </main>
      
      <footer className="bg-black border-t border-zinc-800 py-10 text-center">
        <p className="text-zinc-600 text-sm">© 2026 Natya LMS. All rights reserved.</p>
      </footer>
    </div>
  );
}
