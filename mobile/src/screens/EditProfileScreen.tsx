import React, { useEffect, useState } from 'react';
import { View, Text, TextInput, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView, Alert } from 'react-native';
import client from '../api/client';

// Profile/account editing gap fix. Reuses the EXISTING, already-live
// dj-rest-auth endpoint (GET/PATCH auth/user/, backed by
// CustomUserDetailsSerializer) -- the same endpoint the web /profile page
// now uses. No new backend endpoint. Only the fields rendered here as
// editable inputs are ever sent in the PATCH body; the backend's own
// read_only_fields is what actually enforces which fields can change
// (role/staff/superuser/email/phone_number are rejected server-side
// regardless of what this screen sends).
export default function EditProfileScreen({ navigation }: any) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [email, setEmail] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [form, setForm] = useState({ username: '', first_name: '', last_name: '', parent_name: '', parent_phone: '' });
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const fetchProfile = async () => {
    try {
      setLoadError(false);
      const res = await client.get('auth/user/');
      const data = res.data;
      setEmail(data.email || '');
      setPhoneNumber(data.phone_number || '');
      setForm({
        username: data.username || '',
        first_name: data.first_name || '',
        last_name: data.last_name || '',
        parent_name: data.parent_name || '',
        parent_phone: data.parent_phone || '',
      });
    } catch (err) {
      console.error(err);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchProfile(); }, []);

  const handleSave = async () => {
    setSaving(true);
    setFieldErrors({});
    try {
      await client.patch('auth/user/', {
        username: form.username,
        first_name: form.first_name,
        last_name: form.last_name,
        parent_name: form.parent_name,
        parent_phone: form.parent_phone,
      });
      Alert.alert('Saved', 'Your profile has been updated.', [
        { text: 'OK', onPress: () => navigation.goBack() },
      ]);
    } catch (err: any) {
      const data = err.response?.data;
      if (data && typeof data === 'object') {
        const errors: Record<string, string> = {};
        for (const key of Object.keys(data)) {
          if (Array.isArray(data[key])) errors[key] = data[key].join(' ');
        }
        if (Object.keys(errors).length > 0) {
          setFieldErrors(errors);
        } else {
          Alert.alert('Error', 'Could not update your profile.');
        }
      } else {
        Alert.alert('Error', 'Could not update your profile.');
      }
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        <Text style={styles.headerTitle}>Edit Profile</Text>
        <View style={{ width: 50 }} />
      </View>

      {loadError ? (
        <View style={styles.centered}>
          <Text style={styles.errorText}>Couldn't load your profile.</Text>
          <TouchableOpacity style={styles.retryButton} onPress={() => { setLoading(true); fetchProfile(); }}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={styles.label}>Username</Text>
          <TextInput
            style={styles.input}
            value={form.username}
            onChangeText={(t) => setForm({ ...form, username: t })}
            autoCapitalize="none"
            placeholderTextColor="#52525b"
          />
          {!!fieldErrors.username && <Text style={styles.fieldError}>{fieldErrors.username}</Text>}

          <Text style={styles.label}>First Name</Text>
          <TextInput
            style={styles.input}
            value={form.first_name}
            onChangeText={(t) => setForm({ ...form, first_name: t })}
            placeholderTextColor="#52525b"
          />
          {!!fieldErrors.first_name && <Text style={styles.fieldError}>{fieldErrors.first_name}</Text>}

          <Text style={styles.label}>Last Name</Text>
          <TextInput
            style={styles.input}
            value={form.last_name}
            onChangeText={(t) => setForm({ ...form, last_name: t })}
            placeholderTextColor="#52525b"
          />
          {!!fieldErrors.last_name && <Text style={styles.fieldError}>{fieldErrors.last_name}</Text>}

          {/* Read-only: email/phone_number are the OTP sign-in identity and
              have no verification-on-change flow today -- shown for
              reference only, matching CustomUserDetailsSerializer's own
              read_only_fields exactly. */}
          <Text style={styles.label}>Email</Text>
          <View style={styles.readOnlyInput}><Text style={styles.readOnlyText}>{email || '—'}</Text></View>

          <Text style={styles.label}>Phone Number</Text>
          <View style={styles.readOnlyInput}><Text style={styles.readOnlyText}>{phoneNumber || '—'}</Text></View>
          <Text style={styles.hint}>Email and phone number are tied to your sign-in and can't be changed here.</Text>

          <Text style={styles.sectionLabel}>Parent / Guardian Contact (optional)</Text>

          <Text style={styles.label}>Parent Name</Text>
          <TextInput
            style={styles.input}
            value={form.parent_name}
            onChangeText={(t) => setForm({ ...form, parent_name: t })}
            placeholderTextColor="#52525b"
          />
          {!!fieldErrors.parent_name && <Text style={styles.fieldError}>{fieldErrors.parent_name}</Text>}

          <Text style={styles.label}>Parent Phone</Text>
          <TextInput
            style={styles.input}
            value={form.parent_phone}
            onChangeText={(t) => setForm({ ...form, parent_phone: t })}
            keyboardType="phone-pad"
            placeholderTextColor="#52525b"
          />
          {!!fieldErrors.parent_phone && <Text style={styles.fieldError}>{fieldErrors.parent_phone}</Text>}

          <TouchableOpacity style={styles.saveButton} onPress={handleSave} disabled={saving}>
            {saving ? <ActivityIndicator color="#000" /> : <Text style={styles.saveButtonText}>Save Changes</Text>}
          </TouchableOpacity>
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505', padding: 24 },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16 },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold' },
  content: { padding: 20, paddingBottom: 48 },
  label: { color: '#71717a', fontSize: 12, fontWeight: '600', marginBottom: 6, marginTop: 16, textTransform: 'uppercase', letterSpacing: 0.5 },
  sectionLabel: { color: '#e4e4e7', fontSize: 13, fontWeight: 'bold', marginTop: 28, marginBottom: 4, borderTopWidth: 1, borderTopColor: '#18181b', paddingTop: 20 },
  input: { backgroundColor: '#0a0a0a', borderWidth: 1, borderColor: '#27272a', borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12, color: '#fff', fontSize: 14 },
  readOnlyInput: { backgroundColor: 'rgba(10,10,10,0.5)', borderWidth: 1, borderColor: '#18181b', borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12 },
  readOnlyText: { color: '#71717a', fontSize: 14 },
  hint: { color: '#52525b', fontSize: 11, marginTop: 6 },
  fieldError: { color: '#f87171', fontSize: 12, marginTop: 4 },
  saveButton: { backgroundColor: '#facc15', paddingVertical: 16, borderRadius: 30, alignItems: 'center', marginTop: 32 },
  saveButtonText: { color: '#000', fontSize: 16, fontWeight: 'bold' },
  errorText: { color: '#f87171', fontSize: 15, textAlign: 'center', marginBottom: 16 },
  retryButton: { borderWidth: 1, borderColor: '#facc15', borderRadius: 10, paddingHorizontal: 20, paddingVertical: 10 },
  retryText: { color: '#facc15', fontSize: 13, fontWeight: '700' },
});
