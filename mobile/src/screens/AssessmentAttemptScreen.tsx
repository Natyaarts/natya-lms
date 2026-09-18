import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView, Alert } from 'react-native';
import client from '../api/client';

// Phase 4.9 -- mobile assessment attempt screen. Dual-purpose, mirroring
// the web frontend's assessments/attempt/[attemptId]/page.tsx: renders the
// live question-answering UI for an IN_PROGRESS attempt, or the frozen
// per-question review for a SUBMITTED/TIMED_OUT one -- both come from the
// same GET courses/assessment-attempts/<id>/ endpoint, which returns a
// different shape for each (see _serialize_in_progress_attempt vs
// _serialize_finished_attempt on the backend), so which UI to show is
// entirely server-driven, never guessed here.
export default function AssessmentAttemptScreen({ route, navigation }: any) {
  const { attemptId } = route.params;
  const [attempt, setAttempt] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [answers, setAnswers] = useState<Record<number, Set<number>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = async () => {
    try {
      const res = await client.get(`courses/assessment-attempts/${attemptId}/`);
      setAttempt(res.data);
    } catch (err: any) {
      Alert.alert('Error', err.response?.data?.error || 'Could not load this attempt.');
      navigation.goBack();
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attemptId]);

  useEffect(() => {
    if (attempt?.status !== 'IN_PROGRESS' || !attempt.deadline) {
      setRemainingSeconds(null);
      return;
    }
    const deadlineMs = new Date(attempt.deadline).getTime();
    const tick = () => setRemainingSeconds(Math.max(0, Math.floor((deadlineMs - Date.now()) / 1000)));
    tick();
    timerRef.current = setInterval(tick, 1000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [attempt?.status, attempt?.deadline]);

  const toggleOption = (question: any, optionId: number) => {
    setAnswers((prev) => {
      const next = { ...prev };
      const current = new Set(next[question.id] || []);
      if (question.question_type === 'SINGLE_CHOICE') {
        next[question.id] = current.has(optionId) ? new Set() : new Set([optionId]);
      } else {
        if (current.has(optionId)) current.delete(optionId); else current.add(optionId);
        next[question.id] = current;
      }
      return next;
    });
  };

  const handleSubmit = async () => {
    Alert.alert('Submit Assessment', 'Once submitted, you cannot change your answers. Continue?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Submit', style: 'destructive', onPress: async () => {
          setSubmitting(true);
          try {
            const payload = {
              answers: Object.entries(answers).map(([questionId, optionIds]) => ({
                question_id: Number(questionId),
                option_ids: Array.from(optionIds),
              })),
            };
            const res = await client.post(`courses/assessment-attempts/${attemptId}/submit/`, payload);
            setAttempt(res.data);
          } catch (err: any) {
            Alert.alert('Error', err.response?.data?.error || 'Could not submit this attempt.');
          } finally {
            setSubmitting(false);
          }
        },
      },
    ]);
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;
  if (!attempt) return null;

  const isInProgress = attempt.status === 'IN_PROGRESS';
  const timeUp = remainingSeconds !== null && remainingSeconds <= 0;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        {!isInProgress && (
          <TouchableOpacity onPress={() => navigation.goBack()}><Text style={styles.backText}>← Back</Text></TouchableOpacity>
        )}
        <Text style={styles.headerTitle} numberOfLines={1}>{attempt.assessment?.title}</Text>
        {isInProgress && remainingSeconds !== null && (
          <Text style={[styles.timer, timeUp && styles.timerUp]}>
            {Math.floor(remainingSeconds / 60)}:{String(remainingSeconds % 60).padStart(2, '0')}
          </Text>
        )}
      </View>

      {!isInProgress && (
        <View style={[styles.resultBanner, attempt.passed ? styles.resultPass : styles.resultFail]}>
          <Text style={styles.resultTitle}>{attempt.status === 'TIMED_OUT' ? 'Time Expired' : attempt.passed ? 'Passed' : 'Not Passed'}</Text>
          {attempt.status !== 'TIMED_OUT' && (
            <Text style={styles.resultSubtitle}>
              {attempt.score} / {attempt.total_marks} ({attempt.percentage}%)
            </Text>
          )}
        </View>
      )}

      <ScrollView style={styles.questionsList} contentContainerStyle={{ paddingBottom: 32 }}>
        {attempt.questions?.map((question: any, idx: number) => (
          <View key={question.id} style={styles.questionCard}>
            <Text style={styles.questionText}>{idx + 1}. {question.question_text}</Text>
            {!isInProgress && (
              <Text style={[styles.questionMarks, question.answered_correctly ? styles.markCorrect : styles.markIncorrect]}>
                {question.answered_correctly ? `✓ +${question.marks_awarded} marks` : `✗ 0 / ${question.marks} marks`}
              </Text>
            )}
            {question.options?.map((option: any) => {
              const selected = isInProgress
                ? (answers[question.id]?.has(option.id) ?? false)
                : (question.selected_option_ids || []).includes(option.id);
              const isCorrectAnswer = !isInProgress && option.is_correct_answer;
              return (
                <TouchableOpacity
                  key={option.id}
                  disabled={!isInProgress || timeUp}
                  onPress={() => toggleOption(question, option.id)}
                  style={[
                    styles.optionRow,
                    selected && styles.optionRowSelected,
                    isCorrectAnswer && styles.optionRowCorrect,
                    !isInProgress && selected && !isCorrectAnswer && styles.optionRowWrong,
                  ]}
                >
                  <Text style={styles.optionText}>{option.option_text}</Text>
                  {!isInProgress && isCorrectAnswer && <Text style={styles.optionBadge}>Correct</Text>}
                  {!isInProgress && selected && !isCorrectAnswer && <Text style={styles.optionBadgeWrong}>Your answer</Text>}
                </TouchableOpacity>
              );
            })}
          </View>
        ))}
      </ScrollView>

      {isInProgress ? (
        <TouchableOpacity style={styles.submitButton} onPress={handleSubmit} disabled={submitting}>
          {submitting ? <ActivityIndicator color="#000" /> : <Text style={styles.submitButtonText}>Submit Assessment</Text>}
        </TouchableOpacity>
      ) : (
        <TouchableOpacity style={styles.submitButton} onPress={() => navigation.goBack()}>
          <Text style={styles.submitButtonText}>Done</Text>
        </TouchableOpacity>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505' },
  header: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16, marginRight: 12 },
  headerTitle: { color: '#fff', fontSize: 16, fontWeight: 'bold', flex: 1 },
  timer: { color: '#a1a1aa', fontSize: 16, fontWeight: 'bold', fontVariant: ['tabular-nums'] },
  timerUp: { color: '#f87171' },

  resultBanner: { padding: 20, alignItems: 'center' },
  resultPass: { backgroundColor: 'rgba(34,197,94,0.12)' },
  resultFail: { backgroundColor: 'rgba(248,113,113,0.12)' },
  resultTitle: { color: '#fff', fontSize: 22, fontWeight: 'bold' },
  resultSubtitle: { color: '#a1a1aa', fontSize: 15, marginTop: 4 },

  questionsList: { flex: 1, padding: 16 },
  questionCard: { backgroundColor: '#0a0a0a', borderRadius: 12, borderWidth: 1, borderColor: '#27272a', padding: 16, marginBottom: 16 },
  questionText: { color: '#fff', fontSize: 15, fontWeight: '600', marginBottom: 12 },
  questionMarks: { fontSize: 12, fontWeight: 'bold', marginBottom: 10 },
  markCorrect: { color: '#22c55e' },
  markIncorrect: { color: '#f87171' },

  optionRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#18181b', borderWidth: 1, borderColor: '#27272a', borderRadius: 8, padding: 12, marginBottom: 8 },
  optionRowSelected: { borderColor: '#facc15', backgroundColor: 'rgba(250,204,21,0.08)' },
  optionRowCorrect: { borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)' },
  optionRowWrong: { borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.1)' },
  optionText: { color: '#e4e4e7', fontSize: 14, flex: 1 },
  optionBadge: { color: '#22c55e', fontSize: 11, fontWeight: 'bold' },
  optionBadgeWrong: { color: '#f87171', fontSize: 11, fontWeight: 'bold' },

  submitButton: { backgroundColor: '#facc15', paddingVertical: 16, alignItems: 'center', margin: 16, borderRadius: 30 },
  submitButtonText: { color: '#000', fontSize: 16, fontWeight: 'bold' },
});
