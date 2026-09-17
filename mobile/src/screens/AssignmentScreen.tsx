import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView, TextInput, Alert, Linking } from 'react-native';
import * as DocumentPicker from 'expo-document-picker';
import client from '../api/client';

// Phase 4.9/4.10 -- mobile assignment detail/submission screen, mirroring
// the web frontend's assignments/[assignmentId]/page.tsx contract exactly
// (same three endpoints, same status-driven resubmit gating).
//
// Phase 4.10: file submission via expo-document-picker (the Storage Access
// Framework on Android -- no storage permission needed at all, consistent
// with app.json's existing blockedPermissions for READ_MEDIA_IMAGES/VIDEO).
// ALLOWED_EXTENSIONS/MAX_FILE_SIZE_BYTES below are kept in sync with
// backend/courses/validators.py's ALLOWED_SUBMISSION_FILE_EXTENSIONS/
// MAX_SUBMISSION_FILE_SIZE_BYTES -- checked here purely for fast, friendly
// feedback before ever making a request; the backend remains the sole
// actual enforcement (this check can never be relied on to reject
// anything by itself -- see handleSubmit's own error handling for the
// server's answer, which always has the final say).
const ALLOWED_EXTENSIONS = ['pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png', 'zip'];
const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024; // 10MB

const formatFileSize = (bytes: number) => {
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

function statusMeta(status: string) {
  switch (status) {
    case 'SUBMITTED': return { label: 'Submitted -- Awaiting Grading', color: '#60a5fa' };
    case 'GRADED': return { label: 'Graded', color: '#22c55e' };
    case 'RETURNED_FOR_REVISION': return { label: 'Revision Requested', color: '#fb923c' };
    default: return { label: status, color: '#71717a' };
  }
}

export default function AssignmentScreen({ route, navigation }: any) {
  const { assignmentId } = route.params;
  const [assignment, setAssignment] = useState<any>(null);
  const [submissions, setSubmissions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [content, setContent] = useState('');
  const [selectedFile, setSelectedFile] = useState<DocumentPicker.DocumentPickerAsset | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadSubmissions = async () => {
    const res = await client.get(`courses/assignment-submissions/my/?assignment_id=${assignmentId}`);
    setSubmissions(res.data.results || []);
  };

  useEffect(() => {
    const load = async () => {
      try {
        const res = await client.get(`courses/assignments/${assignmentId}/`);
        setAssignment(res.data);
        await loadSubmissions();
      } catch (err: any) {
        setError(err.response?.data?.error || 'Could not load this assignment.');
      } finally {
        setLoading(false);
      }
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assignmentId]);

  const latest = submissions[0] || null;
  const canSubmit = !latest || latest.status === 'RETURNED_FOR_REVISION';

  const handlePickFile = async () => {
    const result = await DocumentPicker.getDocumentAsync({ type: '*/*', copyToCacheDirectory: true });
    if (result.canceled || !result.assets?.[0]) return;
    const asset = result.assets[0];

    const extension = asset.name.split('.').pop()?.toLowerCase() || '';
    if (!ALLOWED_EXTENSIONS.includes(extension)) {
      Alert.alert(
        'Unsupported File Type',
        `".${extension}" isn't a supported file type. Allowed types: ${ALLOWED_EXTENSIONS.join(', ')}.`
      );
      return;
    }
    if (typeof asset.size === 'number' && asset.size > MAX_FILE_SIZE_BYTES) {
      Alert.alert('File Too Large', `This file is ${formatFileSize(asset.size)}. Maximum size is 10 MB.`);
      return;
    }

    setSelectedFile(asset);
  };

  const handleSubmit = async () => {
    if (!content.trim() && !selectedFile) {
      Alert.alert('Error', 'Provide submission text or a file.');
      return;
    }
    setSubmitting(true);
    try {
      const formData = new FormData();
      if (content.trim()) formData.append('content', content);
      if (selectedFile) {
        // React Native's FormData file-field shape -- {uri, name, type},
        // not a web File/Blob object. Unrelated to the Expo SDK, so this
        // convention doesn't shift between Expo versions the way native
        // module APIs can.
        formData.append('file', {
          uri: selectedFile.uri,
          name: selectedFile.name,
          type: selectedFile.mimeType || 'application/octet-stream',
        } as any);
      }
      await client.post(`courses/assignments/${assignmentId}/submit/`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setContent('');
      setSelectedFile(null);
      await loadSubmissions();
    } catch (err: any) {
      Alert.alert('Error', err.response?.data?.error || 'Could not submit this assignment.');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;

  if (error || !assignment) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}>
          <Text style={styles.errorText}>{error || 'Assignment not found.'}</Text>
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
        <Text style={styles.title}>{assignment.title}</Text>
        {!!assignment.description && <Text style={styles.description}>{assignment.description}</Text>}
        <View style={styles.maxMarksBox}>
          <Text style={styles.maxMarksLabel}>Max Marks</Text>
          <Text style={styles.maxMarksValue}>{assignment.max_marks}</Text>
        </View>

        {latest && (
          <View style={styles.latestBox}>
            <View style={styles.statusRow}>
              <Text style={[styles.statusBadge, { color: statusMeta(latest.status).color }]}>{statusMeta(latest.status).label}</Text>
              <Text style={styles.attemptText}>Attempt {latest.attempt_number}</Text>
            </View>

            {!!latest.content && (
              <View style={styles.submissionBox}>
                <Text style={styles.submissionLabel}>Your Submission</Text>
                <Text style={styles.submissionText}>{latest.content}</Text>
              </View>
            )}
            {!!latest.submitted_file && (
              <TouchableOpacity onPress={() => Linking.openURL(latest.submitted_file)}>
                <Text style={styles.fileLink}>View submitted file</Text>
              </TouchableOpacity>
            )}

            {latest.status === 'GRADED' && (
              <View style={styles.gradeBox}>
                <Text style={styles.gradeText}>Grade: {latest.marks_awarded} / {assignment.max_marks}</Text>
                {!!latest.feedback && <Text style={styles.feedbackText}>{latest.feedback}</Text>}
              </View>
            )}
            {latest.status === 'RETURNED_FOR_REVISION' && !!latest.feedback && (
              <View style={styles.revisionBox}>
                <Text style={styles.revisionLabel}>Feedback</Text>
                <Text style={styles.feedbackText}>{latest.feedback}</Text>
              </View>
            )}
          </View>
        )}

        {canSubmit && (
          <View style={styles.formBox}>
            <Text style={styles.formLabel}>{latest ? 'Resubmit Assignment' : 'Submit Assignment'}</Text>
            <TextInput
              style={styles.textArea}
              placeholder="Write your submission here (optional if attaching a file)..."
              placeholderTextColor="#666"
              value={content}
              onChangeText={setContent}
              multiline
              numberOfLines={6}
              textAlignVertical="top"
            />

            {selectedFile ? (
              <View style={styles.filePickedRow}>
                <Text style={styles.filePickedName} numberOfLines={1}>📎 {selectedFile.name}</Text>
                {typeof selectedFile.size === 'number' && (
                  <Text style={styles.filePickedSize}>{formatFileSize(selectedFile.size)}</Text>
                )}
                <TouchableOpacity onPress={() => setSelectedFile(null)}>
                  <Text style={styles.fileRemoveText}>✕</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <TouchableOpacity style={styles.filePickButton} onPress={handlePickFile}>
                <Text style={styles.filePickButtonText}>📎 Attach a File</Text>
              </TouchableOpacity>
            )}
            <Text style={styles.fileHint}>Allowed: PDF, Word, TXT, JPG, PNG, ZIP -- up to 10 MB.</Text>

            <TouchableOpacity style={styles.submitButton} onPress={handleSubmit} disabled={submitting}>
              {submitting ? <ActivityIndicator color="#000" /> : <Text style={styles.submitButtonText}>{latest ? 'Resubmit' : 'Submit'}</Text>}
            </TouchableOpacity>
          </View>
        )}

        {submissions.length > 1 && (
          <View style={styles.historyBox}>
            <Text style={styles.historyLabel}>Submission History</Text>
            {submissions.map((s) => (
              <View key={s.id} style={styles.historyRow}>
                <Text style={styles.historyAttempt}>Attempt {s.attempt_number}</Text>
                <Text style={[styles.historyStatus, { color: statusMeta(s.status).color }]}>{statusMeta(s.status).label}</Text>
                {s.status === 'GRADED' && <Text style={styles.historyGrade}>{s.marks_awarded} / {assignment.max_marks}</Text>}
              </View>
            ))}
          </View>
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
  title: { color: '#fff', fontSize: 24, fontWeight: 'bold', marginBottom: 8 },
  description: { color: '#a1a1aa', fontSize: 14, marginBottom: 16, lineHeight: 20 },
  maxMarksBox: { backgroundColor: '#18181b', borderRadius: 12, padding: 12, alignSelf: 'flex-start', marginBottom: 20 },
  maxMarksLabel: { color: '#71717a', fontSize: 11 },
  maxMarksValue: { color: '#fff', fontSize: 16, fontWeight: 'bold' },

  latestBox: { marginBottom: 20 },
  statusRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
  statusBadge: { fontSize: 12, fontWeight: 'bold', marginRight: 10 },
  attemptText: { color: '#71717a', fontSize: 12 },
  submissionBox: { marginBottom: 10 },
  submissionLabel: { color: '#71717a', fontSize: 11, fontWeight: 'bold', marginBottom: 4 },
  submissionText: { color: '#e4e4e7', fontSize: 14, backgroundColor: '#0a0a0a', borderRadius: 8, padding: 12, lineHeight: 20 },
  fileLink: { color: '#facc15', fontSize: 13, marginBottom: 10 },
  gradeBox: { backgroundColor: 'rgba(34,197,94,0.1)', borderRadius: 10, padding: 14 },
  gradeText: { color: '#22c55e', fontSize: 14, fontWeight: 'bold', marginBottom: 4 },
  revisionBox: { backgroundColor: 'rgba(251,146,60,0.1)', borderRadius: 10, padding: 14 },
  revisionLabel: { color: '#fb923c', fontSize: 13, fontWeight: 'bold', marginBottom: 4 },
  feedbackText: { color: '#e4e4e7', fontSize: 13, lineHeight: 19 },

  formBox: { marginBottom: 24 },
  formLabel: { color: '#e4e4e7', fontSize: 14, fontWeight: 'bold', marginBottom: 10 },
  textArea: { backgroundColor: '#0a0a0a', borderWidth: 1, borderColor: '#27272a', borderRadius: 10, padding: 12, color: '#fff', fontSize: 14, minHeight: 120, marginBottom: 12 },
  filePickButton: { backgroundColor: '#18181b', borderWidth: 1, borderColor: '#27272a', borderRadius: 10, paddingVertical: 12, alignItems: 'center', marginBottom: 6 },
  filePickButtonText: { color: '#e4e4e7', fontSize: 13, fontWeight: '600' },
  filePickedRow: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#18181b', borderWidth: 1, borderColor: 'rgba(250,204,21,0.4)', borderRadius: 10, paddingVertical: 10, paddingHorizontal: 12, marginBottom: 6 },
  filePickedName: { color: '#facc15', fontSize: 13, flex: 1 },
  filePickedSize: { color: '#71717a', fontSize: 11, marginHorizontal: 8 },
  fileRemoveText: { color: '#f87171', fontSize: 14, fontWeight: 'bold', paddingHorizontal: 4 },
  fileHint: { color: '#52525b', fontSize: 11, marginBottom: 14 },
  submitButton: { backgroundColor: '#facc15', paddingVertical: 14, borderRadius: 30, alignItems: 'center' },
  submitButtonText: { color: '#000', fontSize: 15, fontWeight: 'bold' },

  historyBox: { borderTopWidth: 1, borderTopColor: '#27272a', paddingTop: 16 },
  historyLabel: { color: '#71717a', fontSize: 11, fontWeight: 'bold', marginBottom: 10 },
  historyRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#0a0a0a', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8, marginBottom: 6 },
  historyAttempt: { color: '#a1a1aa', fontSize: 13 },
  historyStatus: { fontSize: 11, fontWeight: 'bold' },
  historyGrade: { color: '#e4e4e7', fontSize: 13 },
});
