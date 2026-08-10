"use client";

import { useEffect, useState } from "react";
import { Plus, Edit2, Trash2, X, PlusCircle, Trash } from "lucide-react";

export default function OnboardingFieldsManager() {
  const [fields, setFields] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  // Modals state
  const [showModal, setShowModal] = useState(false);
  const [editingField, setEditingField] = useState<any>(null); // null = adding, object = editing
  const [submitting, setSubmitting] = useState(false);

  // Form Fields State
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const [fieldType, setFieldType] = useState("text");
  const [isRequired, setIsRequired] = useState(true);
  const [order, setOrder] = useState(0);
  const [dropdownOptions, setDropdownOptions] = useState<string[]>([]);
  const [newOption, setNewOption] = useState("");
  const [formError, setFormError] = useState("");

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

  const fetchFields = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/onboarding-fields-admin/`, {
        credentials: "include"
      });
      if (res.ok) {
        const data = await res.json();
        setFields(data);
      } else {
        setError("Failed to fetch onboarding fields");
      }
    } catch (err) {
      setError("Network error fetching onboarding fields");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFields();
  }, []);

  const openAddModal = () => {
    setEditingField(null);
    setName("");
    setLabel("");
    setFieldType("text");
    setIsRequired(true);
    setOrder(fields.length); // auto order recommendation
    setDropdownOptions([]);
    setNewOption("");
    setFormError("");
    setShowModal(true);
  };

  const openEditModal = (field: any) => {
    setEditingField(field);
    setName(field.name);
    setLabel(field.label);
    setFieldType(field.field_type);
    setIsRequired(field.is_required);
    setOrder(field.order);
    setDropdownOptions(field.options || []);
    setNewOption("");
    setFormError("");
    setShowModal(true);
  };

  const handleAddOption = () => {
    const trimmed = newOption.trim();
    if (!trimmed) return;
    if (dropdownOptions.includes(trimmed)) {
      setFormError("Option already exists.");
      return;
    }
    setDropdownOptions([...dropdownOptions, trimmed]);
    setNewOption("");
    setFormError("");
  };

  const handleRemoveOption = (index: number) => {
    setDropdownOptions(dropdownOptions.filter((_, idx) => idx !== index));
  };

  const handleSaveField = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");

    // Validate Name
    const nameRegex = /^[a-z0-9_]+$/;
    if (!nameRegex.test(name)) {
      setFormError("Name must be lowercase alphanumeric and underscores only, no spaces.");
      return;
    }

    if (fieldType === "dropdown" && dropdownOptions.length === 0) {
      setFormError("Dropdown fields must have at least one option configured.");
      return;
    }

    setSubmitting(true);

    const payload = {
      name,
      label,
      field_type: fieldType,
      is_required: isRequired,
      order: parseInt(order as any, 10) || 0,
      options: fieldType === "dropdown" ? dropdownOptions : null
    };

    const url = editingField
      ? `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/onboarding-fields-admin/${editingField.id}/`
      : `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/onboarding-fields-admin/`;

    const method = editingField ? "PUT" : "POST";

    try {
      const res = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify(payload),
        credentials: "include"
      });

      const data = await res.json();
      if (res.ok) {
        setShowModal(false);
        fetchFields();
      } else {
        if (typeof data === "object") {
          const errors = Object.keys(data).map(k => `${k}: ${data[k]}`).join(" | ");
          setFormError(errors);
        } else {
          setFormError("Failed to save field configuration.");
        }
      }
    } catch (err) {
      setFormError("Network error saving configuration.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteField = async (id: number, label: string) => {
    if (!confirm(`Are you sure you want to delete the onboarding field "${label}"? This will stop gathering this info from new learners.`)) return;

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/onboarding-fields-admin/${id}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });

      if (res.ok) {
        fetchFields();
      } else {
        alert("Failed to delete onboarding field.");
      }
    } catch (err) {
      console.error(err);
      alert("Error deleting onboarding field.");
    }
  };

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-8 gap-4">
        <div>
          <h1 className="text-3xl font-bold">Onboarding Fields</h1>
          <p className="text-zinc-400 text-sm mt-1">Configure profile inputs requested from students during registration onboarding.</p>
        </div>
        <button
          onClick={openAddModal}
          className="flex items-center gap-2 px-5 py-3 bg-[#facc15] hover:bg-yellow-500 text-black font-bold rounded-xl shadow-lg transition-colors text-sm"
        >
          <Plus className="w-4 h-4" /> Add Field
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Fields Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-xs text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Label</th>
                <th className="p-4 font-semibold">Variable Name</th>
                <th className="p-4 font-semibold">Type</th>
                <th className="p-4 font-semibold text-center">Required</th>
                <th className="p-4 font-semibold text-center">Order</th>
                <th className="p-4 font-semibold">Dropdown Options</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-sm text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span>Loading onboarding configuration...</span>
                    </div>
                  </td>
                </tr>
              ) : fields.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    No onboarding fields configured. New users will skip onboarding automatically.
                  </td>
                </tr>
              ) : (
                fields.map(field => (
                  <tr key={field.id} className="hover:bg-white/5 transition-colors">
                    {/* Label */}
                    <td className="p-4 font-bold text-white">{field.label}</td>
                    
                    {/* Variable Name */}
                    <td className="p-4 text-xs font-mono text-zinc-400">@{field.name}</td>
                    
                    {/* Field Type */}
                    <td className="p-4">
                      <span className="px-2 py-0.5 bg-zinc-800 text-zinc-300 border border-white/5 rounded text-xs font-semibold capitalize">
                        {field.field_type}
                      </span>
                    </td>

                    {/* Required Check */}
                    <td className="p-4 text-center">
                      <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${
                        field.is_required 
                          ? 'bg-red-500/10 text-red-400 border border-red-500/20' 
                          : 'bg-zinc-800 text-zinc-500'
                      }`}>
                        {field.is_required ? 'REQUIRED' : 'OPTIONAL'}
                      </span>
                    </td>

                    {/* Display Order */}
                    <td className="p-4 text-center font-bold text-white">{field.order}</td>

                    {/* Dropdown Options Preview */}
                    <td className="p-4 text-xs max-w-xs truncate">
                      {field.field_type === 'dropdown' && field.options ? (
                        <div className="flex flex-wrap gap-1">
                          {field.options.map((opt: string) => (
                            <span key={opt} className="px-1.5 py-0.5 bg-white/5 rounded text-[10px] text-zinc-400 border border-white/5">
                              {opt}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-zinc-600 italic">N/A</span>
                      )}
                    </td>

                    {/* Actions */}
                    <td className="p-4 text-center">
                      <div className="flex items-center justify-center gap-2">
                        <button
                          onClick={() => openEditModal(field)}
                          className="p-2 bg-white/5 border border-white/5 hover:border-white/15 rounded-xl transition-colors inline-flex items-center justify-center text-zinc-400 hover:text-[#facc15]"
                          title="Edit details"
                        >
                          <Edit2 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDeleteField(field.id, field.label)}
                          className="p-2 bg-white/5 border border-white/5 hover:border-white/15 rounded-xl transition-colors inline-flex items-center justify-center text-zinc-400 hover:text-red-500"
                          title="Delete field"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create / Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-lg shadow-2xl relative">
            <button
              onClick={() => setShowModal(false)}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">
              {editingField ? "Edit Onboarding Field" : "Create Onboarding Field"}
            </h2>
            <p className="text-zinc-400 text-xs mb-6">Configure custom profile input attributes.</p>

            {formError && (
              <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl mb-4 text-xs font-normal">
                {formError}
              </div>
            )}

            <form onSubmit={handleSaveField} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Variable Name *</label>
                  <input
                    type="text"
                    required
                    disabled={!!editingField} // Lock key mapping to avoid breaking active profile datasets
                    placeholder="e.g. dob, address"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15] disabled:opacity-50"
                  />
                  <p className="text-[10px] text-zinc-500 mt-1">Lowercase alphanumeric and underscores only.</p>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Display Label *</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Date of Birth"
                    value={label}
                    onChange={e => setLabel(e.target.value)}
                    className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Field Type</label>
                  <select
                    value={fieldType}
                    onChange={e => setFieldType(e.target.value)}
                    className="w-full bg-zinc-800 border border-white/10 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
                  >
                    <option value="text">Short Text</option>
                    <option value="textarea">Long Text</option>
                    <option value="date">Date Picker</option>
                    <option value="dropdown">Dropdown Select</option>
                    <option value="checkbox">Checkbox (True/False)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Display Order</label>
                  <input
                    type="number"
                    required
                    value={order}
                    onChange={e => setOrder(parseInt(e.target.value) || 0)}
                    className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>
              </div>

              <div className="flex items-center gap-3 pt-2">
                <input
                  type="checkbox"
                  id="isRequired"
                  checked={isRequired}
                  onChange={e => setIsRequired(e.target.checked)}
                  className="w-5 h-5 rounded border-zinc-800 bg-zinc-900 text-[#facc15] focus:ring-[#facc15] focus:ring-offset-black cursor-pointer"
                />
                <label htmlFor="isRequired" className="text-sm font-semibold text-zinc-300 cursor-pointer">
                  Require input verification from learner
                </label>
              </div>

              {fieldType === "dropdown" && (
                <div className="pt-4 border-t border-white/5 space-y-3">
                  <h3 className="text-sm font-bold text-zinc-300">Dropdown Options</h3>
                  
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="Add dropdown option"
                      value={newOption}
                      onChange={e => setNewOption(e.target.value)}
                      className="flex-1 bg-zinc-850 border border-white/5 rounded-xl px-4 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
                    />
                    <button
                      type="button"
                      onClick={handleAddOption}
                      className="flex items-center gap-1.5 px-4 py-2 bg-white/5 border border-white/5 hover:border-white/15 rounded-xl text-xs font-bold text-white transition-all"
                    >
                      <PlusCircle className="w-3.5 h-3.5" /> Add
                    </button>
                  </div>

                  <div className="space-y-1 max-h-32 overflow-y-auto pr-1">
                    {dropdownOptions.map((opt, index) => (
                      <div key={index} className="flex justify-between items-center p-2 bg-white/5 border border-white/5 rounded-lg text-xs font-medium">
                        <span className="text-zinc-300">{opt}</span>
                        <button
                          type="button"
                          onClick={() => handleRemoveOption(index)}
                          className="text-red-500 hover:text-red-400 p-1 rounded hover:bg-red-500/10 transition-all"
                          title="Remove option"
                        >
                          <Trash className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                    {dropdownOptions.length === 0 && (
                      <p className="text-[10px] text-zinc-500 italic">No options added yet. Dropdown needs at least one option.</p>
                    )}
                  </div>
                </div>
              )}

              <div className="flex justify-end gap-3 pt-6 border-t border-white/5">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2.5 text-sm font-semibold text-zinc-400 hover:text-white transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-5 py-2.5 bg-[#facc15] hover:bg-yellow-500 text-black font-bold rounded-xl transition-colors disabled:opacity-50 text-sm"
                >
                  {submitting ? "Saving..." : "Save Configuration"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
