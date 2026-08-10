"use client";

import { useEffect, useState } from "react";
import { Plus, Edit2, Trash2, Save, X } from "lucide-react";

export default function CMSEditor() {
  // Hero settings state
  const [heroId, setHeroId] = useState<number | null>(null);
  const [heroTitle, setHeroTitle] = useState("");
  const [heroSubtitle, setHeroSubtitle] = useState("");
  const [heroDescription, setHeroDescription] = useState("");
  const [heroButtonText, setHeroButtonText] = useState("");
  const [heroButtonLink, setHeroButtonLink] = useState("");
  const [heroBgImageUrl, setHeroBgImageUrl] = useState("");
  const [savingHero, setSavingHero] = useState(false);

  // Features list state
  const [features, setFeatures] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // Feature modal state
  const [showFeatureModal, setShowFeatureModal] = useState(false);
  const [editingFeature, setEditingFeature] = useState<any>(null);
  const [featureTitle, setFeatureTitle] = useState("");
  const [featureDescription, setFeatureDescription] = useState("");
  const [featureOrder, setFeatureOrder] = useState(0);
  const [savingFeature, setSavingFeature] = useState(false);

  const getCsrfToken = () => {
    let csrfToken = "";
    if (typeof document !== 'undefined' && document.cookie) {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.startsWith('csrftoken=')) {
          csrfToken = decodeURIComponent(cookie.substring('csrftoken='.length));
          break;
        }
      }
    }
    return csrfToken;
  };

  const fetchCMSData = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/cms/landing-page/`, {
        credentials: "include"
      });

      if (res.ok) {
        const data = await res.json();
        
        // Load hero section values
        if (data.hero) {
          setHeroId(data.hero.id);
          setHeroTitle(data.hero.title || "");
          setHeroSubtitle(data.hero.subtitle || "");
          setHeroDescription(data.hero.description || "");
          setHeroButtonText(data.hero.button_text || "");
          setHeroButtonLink(data.hero.button_link || "");
          setHeroBgImageUrl(data.hero.bg_image_url || "");
        }
        
        // Load features
        setFeatures(data.features || []);
      } else {
        setError("Failed to fetch landing page settings.");
      }
    } catch (err) {
      setError("Network error fetching CMS settings.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCMSData();
  }, []);

  const handleSaveHero = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingHero(true);
    setSuccessMsg("");
    setError("");

    try {
      // Patch hero record (singleton is id=1 or loaded ID)
      const targetId = heroId || 1;
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/cms/hero-admin/${targetId}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({
          title: heroTitle,
          subtitle: heroSubtitle,
          description: heroDescription,
          button_text: heroButtonText,
          button_link: heroButtonLink,
          bg_image_url: heroBgImageUrl
        }),
        credentials: "include"
      });

      if (res.ok) {
        setSuccessMsg("Hero Section settings saved successfully!");
        fetchCMSData();
      } else {
        setError("Failed to update Hero Section settings.");
      }
    } catch (err) {
      setError("Error sending save request.");
    } finally {
      setSavingHero(false);
    }
  };

  const handleOpenAddFeature = () => {
    setEditingFeature(null);
    setFeatureTitle("");
    setFeatureDescription("");
    // Default order is next highest order index
    const maxOrder = features.reduce((max, f) => (f.order > max ? f.order : max), 0);
    setFeatureOrder(maxOrder + 1);
    setShowFeatureModal(true);
  };

  const handleOpenEditFeature = (feature: any) => {
    setEditingFeature(feature);
    setFeatureTitle(feature.title || "");
    setFeatureDescription(feature.description || "");
    setFeatureOrder(feature.order || 0);
    setShowFeatureModal(true);
  };

  const handleSaveFeature = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingFeature(true);
    setError("");

    const payload = {
      title: featureTitle,
      description: featureDescription,
      order: featureOrder
    };

    try {
      let res;
      if (editingFeature) {
        // Edit existing feature
        res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/cms/features-admin/${editingFeature.id}/`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken()
          },
          body: JSON.stringify(payload),
          credentials: "include"
        });
      } else {
        // Create new feature
        res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/cms/features-admin/`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken()
          },
          body: JSON.stringify(payload),
          credentials: "include"
        });
      }

      if (res.ok) {
        setShowFeatureModal(false);
        fetchCMSData();
      } else {
        setError("Failed to save feature.");
      }
    } catch (err) {
      setError("Error saving feature.");
    } finally {
      setSavingFeature(false);
    }
  };

  const handleDeleteFeature = async (featureId: number, title: string) => {
    if (!confirm(`Are you sure you want to delete the landing feature "${title}"?`)) return;

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/cms/features-admin/${featureId}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });

      if (res.ok) {
        fetchCMSData();
      } else {
        alert("Failed to delete feature.");
      }
    } catch (err) {
      alert("Error deleting feature.");
    }
  };

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">CMS Editor</h1>
        <p className="text-zinc-400 text-sm mt-1">Configure landing banners, headings, sub-features, and button text values dynamically.</p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}
      {successMsg && <div className="bg-green-500/10 border border-green-500/20 text-green-400 p-4 rounded-xl mb-6 text-sm">{successMsg}</div>}

      {loading ? (
        <div className="flex flex-col items-center justify-center p-20 gap-3">
          <div className="w-8 h-8 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-zinc-500">Loading CMS configurations...</span>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Hero Settings Form - Left column (2/3 width) */}
          <div className="lg:col-span-2 space-y-6">
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 shadow-2xl">
              <h2 className="text-xl font-bold mb-1">Hero Section Settings</h2>
              <p className="text-zinc-500 text-xs mb-6">Modify the main introduction heading and banner images displayed on the homepage.</p>

              <form onSubmit={handleSaveHero} className="space-y-4 text-xs">
                {/* Title */}
                <div>
                  <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">Main Heading / Title</label>
                  <input
                    type="text"
                    required
                    value={heroTitle}
                    onChange={(e) => setHeroTitle(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
                  />
                </div>

                {/* Subtitle */}
                <div>
                  <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">Subtitle</label>
                  <input
                    type="text"
                    required
                    value={heroSubtitle}
                    onChange={(e) => setHeroSubtitle(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
                  />
                </div>

                {/* Description */}
                <div>
                  <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">Description Paragraph</label>
                  <textarea
                    required
                    rows={4}
                    value={heroDescription}
                    onChange={(e) => setHeroDescription(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors resize-none"
                  />
                </div>

                {/* CTA Buttons Row */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">CTA Button Text</label>
                    <input
                      type="text"
                      required
                      value={heroButtonText}
                      onChange={(e) => setHeroButtonText(e.target.value)}
                      className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
                    />
                  </div>
                  <div>
                    <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">CTA Link URL</label>
                    <input
                      type="text"
                      required
                      value={heroButtonLink}
                      onChange={(e) => setHeroButtonLink(e.target.value)}
                      className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
                    />
                  </div>
                </div>

                {/* Background Image URL */}
                <div>
                  <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">Background Image URL</label>
                  <input
                    type="text"
                    required
                    value={heroBgImageUrl}
                    onChange={(e) => setHeroBgImageUrl(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
                  />
                </div>

                <div className="pt-4 border-t border-white/5 flex justify-end">
                  <button
                    type="submit"
                    disabled={savingHero}
                    className="px-6 py-3 bg-[#facc15] hover:bg-yellow-500 disabled:opacity-50 text-black font-bold rounded-xl transition-all flex items-center gap-2"
                  >
                    <Save className="w-4 h-4" />
                    {savingHero ? "Saving..." : "Save Changes"}
                  </button>
                </div>
              </form>
            </div>
          </div>

          {/* Features Manager - Right column (1/3 width) */}
          <div className="lg:col-span-1 space-y-6">
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 shadow-2xl">
              <div className="flex items-center justify-between mb-1">
                <h2 className="text-xl font-bold">Landing Features</h2>
                <button
                  onClick={handleOpenAddFeature}
                  className="p-1.5 bg-[#facc15]/10 hover:bg-[#facc15]/20 text-[#facc15] border border-[#facc15]/20 hover:border-[#facc15]/30 rounded-lg transition-colors inline-flex items-center"
                  title="Add Feature"
                >
                  <Plus className="w-4 h-4" />
                </button>
              </div>
              <p className="text-zinc-500 text-xs mb-6">Create, reorder, or update value cards shown below the main header section.</p>

              <div className="space-y-3">
                {features.length === 0 ? (
                  <div className="p-8 text-center text-zinc-500 border border-dashed border-white/10 rounded-xl text-sm">
                    No features configured.
                  </div>
                ) : (
                  features.map((feature) => (
                    <div
                      key={feature.id}
                      className="p-4 bg-black/40 border border-white/5 rounded-xl hover:border-white/10 transition-colors flex items-start justify-between gap-4"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="px-1.5 py-0.5 bg-zinc-800 border border-white/5 text-[9px] font-bold text-zinc-400 rounded-md font-mono">
                            Order {feature.order}
                          </span>
                          <h4 className="font-bold text-white text-sm truncate">{feature.title}</h4>
                        </div>
                        <p className="text-zinc-400 text-xs mt-1.5 line-clamp-2">{feature.description}</p>
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          onClick={() => handleOpenEditFeature(feature)}
                          className="p-1.5 bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-white rounded-lg transition-colors"
                          title="Edit"
                        >
                          <Edit2 className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => handleDeleteFeature(feature.id, feature.title)}
                          className="p-1.5 bg-white/5 hover:bg-red-500/10 text-zinc-400 hover:text-red-500 rounded-lg transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Feature Modal */}
      {showFeatureModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm text-white">
            <button
              onClick={() => setShowFeatureModal(false)}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">{editingFeature ? "Edit Feature" : "Add Feature"}</h2>
            <p className="text-zinc-500 text-xs mb-6">Landing section feature details card settings.</p>

            <form onSubmit={handleSaveFeature} className="space-y-4 text-xs">
              {/* Title */}
              <div>
                <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">Feature Title</label>
                <input
                  type="text"
                  required
                  value={featureTitle}
                  onChange={(e) => setFeatureTitle(e.target.value)}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
                  placeholder="e.g. Masterclass Videos"
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">Description</label>
                <textarea
                  required
                  rows={3}
                  value={featureDescription}
                  onChange={(e) => setFeatureDescription(e.target.value)}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors resize-none"
                  placeholder="Explain this value offer briefly..."
                />
              </div>

              {/* Order */}
              <div>
                <label className="block text-zinc-400 font-semibold mb-1.5 uppercase tracking-wider">Sorting Order Index</label>
                <input
                  type="number"
                  required
                  value={featureOrder}
                  onChange={(e) => setFeatureOrder(parseInt(e.target.value) || 0)}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
                />
              </div>

              <div className="pt-4 border-t border-white/5 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowFeatureModal(false)}
                  className="px-4 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 font-semibold rounded-xl transition-all"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={savingFeature}
                  className="px-5 py-2.5 bg-[#facc15] hover:bg-yellow-500 disabled:opacity-50 text-black font-bold rounded-xl transition-all flex items-center gap-2"
                >
                  <Save className="w-4 h-4" />
                  {savingFeature ? "Saving..." : "Save Feature"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
