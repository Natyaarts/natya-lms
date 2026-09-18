import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView, Alert } from 'react-native';
import client from '../api/client';

// Phase 4.9 -- mobile assessment "start" screen. Mirrors the web frontend's
// assessments/[assessmentId]/page.tsx: a read-only preview
// (GET courses/assessments/<id>/) shown before the student commits to
// starting, since starting begins the server-side time-limit clock.
export default function AssessmentScreen({ route, navigation }: any) {
  const { assessmentId } = route.params;
  const [preview, setPreview] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await client.get(`courses/assessments/${assessmentId}/`);
        setPreview(res.data);
      } catch (err: any) {
        setError(err.response?.data?.error || 'Could not load this assessment.');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [assessmentId]);

  const handleStart = async () => {
    if (preview?.active_attempt_id) {
      navigation.navigate('AssessmentAttempt', { attemptId: preview.active_attempt_id });
      return;
    }
    setStarting(true);
    try {
      const res = await client.post(`courses/assessments/${assessmentId}/start/`);
      navigation.navigate('AssessmentAttempt', { attemptId: res.data.attempt_id });
    } catch (err: any) {
      Alert.alert('Error', err.response?.data?.error || 'Could not start this assessment.');
    } finally {
      setStarting(false);
    }
  };

  if (loading) {
    return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;
  }

  if (error || !preview) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}>
          <Text style={styles.errorText}>{error || 'Assessment not found.'}</Text>
          <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backLink}>Go Back</Text></TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
      </View>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>{preview.title}</Text>
        {!!preview.description && <Text style={styles.description}>{preview.description}</Text>}

        <View style={styles.statsGrid}>
          <View style={styles.statBox}>
            <Text style={styles.statLabel}>Questions</Text>
            <Text style={styles.statValue}>{preview.question_count}</Text>
          </View>
          <View style={styles.statBox}>
            <Text style={styles.statLabel}>Passing Score</Text>
            <Text style={styles.statValue}>{preview.passing_percentage}%</Text>
          </View>
          <View style={styles.statBox}>
            <Text style={styles.statLabel}>Attempts</Text>
            <Text style={styles.statValue}>{preview.attempts_used} / {preview.max_attempts}</Text>
          </View>
          <View style={styles.statBox}>
            <Text style={styles.statLabel}>Time Limit</Text>
            <Text style={styles.statValue}>{preview.time_limit_minutes ? `${preview.time_limit_minutes} min` : 'None'}</Text>
          </View>
        </View>

        {!!preview.instructions && (
          <View style={styles.instructionsBox}>
            <Text style={styles.instructionsLabel}>Instructions</Text>
            <Text style={styles.instructionsText}>{preview.instructions}</Text>
          </View>
        )}

        {preview.active_attempt_id ? (
          <TouchableOpacity style={styles.primaryButton} onPress={handleStart}>
            <Text style={styles.primaryButtonText}>Continue Attempt</Text>
          </TouchableOpacity>
        ) : preview.can_start ? (
          <TouchableOpacity style={styles.primaryButton} onPress={handleStart} disabled={starting}>
            {starting ? <ActivityIndicator color="#000" /> : <Text style={styles.primaryButtonText}>Start Assessment</Text>}
          </TouchableOpacity>
        ) : (
          <View style={styles.disabledButton}>
            <Text style={styles.disabledButtonText}>Maximum Attempts Reached</Text>
          </View>
        )}

        {/* Attempt-history gap fix -- only worth offering once at least one
            attempt exists (reuses preview.attempts_used, already returned
            by this same endpoint, no extra request needed to decide
            whether to show this). */}
        {preview.attempts_used > 0 && (
          <TouchableOpacity
            style={styles.historyButton}
            onPress={() => navigation.navigate('AttemptHistory', { assessmentId, assessmentTitle: preview.title })}
          >
            <Text style={styles.historyButtonText}>View Previous Attempts</Text>
          </TouchableOpacity>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505', padding: 24 },
  errorText: { color: '#f87171', fontSize: 15, textAlign: 'center', marginBottom: 16 },
  backLink: { color: '#facc15', fontSize: 14 },
  header: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16 },
  content: { padding: 24 },
  title: { color: '#fff', fontSize: 26, fontWeight: 'bold', marginBottom: 8 },
  description: { color: '#a1a1aa', fontSize: 15, marginBottom: 24, lineHeight: 22 },
  statsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginBottom: 24 },
  statBox: { width: '47%', backgroundColor: '#18181b', borderRadius: 12, padding: 14, borderWidth: 1, borderColor: '#27272a' },
  statLabel: { color: '#71717a', fontSize: 12 },
  statValue: { color: '#fff', fontSize: 18, fontWeight: 'bold', marginTop: 4 },
  instructionsBox: { marginBottom: 24 },
  instructionsLabel: { color: '#e4e4e7', fontSize: 14, fontWeight: 'bold', marginBottom: 6 },
  instructionsText: { color: '#a1a1aa', fontSize: 14, lineHeight: 20 },
  primaryButton: { backgroundColor: '#facc15', paddingVertical: 16, borderRadius: 30, alignItems: 'center' },
  primaryButtonText: { color: '#000', fontSize: 16, fontWeight: 'bold' },
  disabledButton: { backgroundColor: '#18181b', paddingVertical: 16, borderRadius: 30, alignItems: 'center' },
  disabledButtonText: { color: '#71717a', fontSize: 16, fontWeight: 'bold' },
  historyButton: { marginTop: 12, paddingVertical: 14, borderRadius: 30, alignItems: 'center', borderWidth: 1, borderColor: '#27272a' },
  historyButtonText: { color: '#facc15', fontSize: 14, fontWeight: '600' },
});
