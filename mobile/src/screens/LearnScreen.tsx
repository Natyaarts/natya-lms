import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, SafeAreaView, ActivityIndicator, ScrollView, Linking, Image } from 'react-native';
import client from '../api/client';
import CustomVideoPlayer from '../components/CustomVideoPlayer';
import { usePreventScreenCapture } from 'expo-screen-capture';

export default function LearnScreen({ route, navigation }: any) {
  usePreventScreenCapture();
  const { courseId, isEnrolled } = route.params;
  const [course, setCourse] = useState<any>(null);
  const [activeLesson, setActiveLesson] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const fetchCourse = async () => {
    try {
      const res = await client.get(`courses/${courseId}/`);
      setCourse(res.data);
      if (isEnrolled && res.data.modules?.length > 0 && res.data.modules[0].lessons?.length > 0) {
        setActiveLesson(res.data.modules[0].lessons[0]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCourse();
  }, [courseId]);

  const videoSource = activeLesson?.video_file 
    ? (activeLesson.video_file.startsWith('/') ? `https://academy-api.natyaarts.com${activeLesson.video_file}` : activeLesson.video_file)
    : null;

  const handlePurchase = () => {
    // Redirect to web app for purchase to bypass app store 30% fee
    Linking.openURL(`https://academy.natyaarts.com/courses/${courseId}`);
  };

  if (loading) return <View style={styles.centered}><ActivityIndicator color="#facc15" size="large" /></View>;
  if (!course) return <View style={styles.centered}><Text style={{color: '#fff'}}>Course not found</Text></View>;

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => navigation.goBack()}>
          <Text style={styles.backText}>← Back</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>{course.title}</Text>
      </View>

      <View style={styles.videoContainer}>
        {!isEnrolled ? (
          <>
            <Image 
              source={{ uri: course.thumbnail?.startsWith('/') ? `https://academy-api.natyaarts.com${course.thumbnail}` : (course.thumbnail || 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop') }} 
              style={styles.previewImage} 
            />
            <View style={styles.previewOverlay}>
              <TouchableOpacity style={styles.purchaseButton} onPress={handlePurchase}>
                <Text style={styles.purchaseButtonText}>Purchase for ₹{course.price}</Text>
              </TouchableOpacity>
              <Text style={styles.secureCheckoutText}>Secure Checkout via Web</Text>
            </View>
          </>
        ) : videoSource ? (
          <CustomVideoPlayer source={videoSource} />
        ) : (
          <View style={styles.noVideo}><Text style={{color: '#666'}}>No video uploaded for this lesson</Text></View>
        )}
      </View>

      <ScrollView style={styles.curriculum}>
        <View style={styles.courseDetails}>
          <Text style={styles.courseDescription}>{course.description}</Text>
        </View>

        <Text style={styles.curriculumHeader}>Course Curriculum</Text>
        {course.modules?.map((module: any, idx: number) => (
          <View key={module.id} style={styles.moduleCard}>
            <Text style={styles.moduleTitle}>Module {idx + 1}: {module.title}</Text>
            {module.lessons?.map((lesson: any, lIdx: number) => (
              <TouchableOpacity 
                key={lesson.id} 
                style={[
                  styles.lessonRow, 
                  activeLesson?.id === lesson.id && styles.activeLessonRow,
                  !isEnrolled && styles.lockedLessonRow
                ]}
                onPress={() => isEnrolled && setActiveLesson(lesson)}
                disabled={!isEnrolled}
              >
                <Text style={[
                  styles.lessonText, 
                  activeLesson?.id === lesson.id && styles.activeLessonText,
                  !isEnrolled && styles.lockedLessonText
                ]}>
                  {lIdx + 1}. {lesson.title} {!isEnrolled && "🔒"}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#050505' },
  header: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  backText: { color: '#facc15', fontSize: 16, marginRight: 16 },
  headerTitle: { color: '#fff', fontSize: 18, fontWeight: 'bold', flex: 1 },
  videoContainer: { width: '100%', aspectRatio: 16/9, backgroundColor: '#000', position: 'relative' },
  noVideo: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  
  // Preview Overlay
  previewImage: { width: '100%', height: '100%', resizeMode: 'cover', opacity: 0.5 },
  previewOverlay: { position: 'absolute', inset: 0, justifyContent: 'center', alignItems: 'center', backgroundColor: 'rgba(0,0,0,0.6)' },
  purchaseButton: { backgroundColor: '#facc15', paddingHorizontal: 24, paddingVertical: 12, borderRadius: 30 },
  purchaseButtonText: { color: '#000', fontSize: 16, fontWeight: 'bold' },
  secureCheckoutText: { color: '#a1a1aa', fontSize: 12, marginTop: 8 },

  curriculum: { flex: 1, padding: 16 },
  courseDetails: { marginBottom: 24 },
  courseDescription: { color: '#a1a1aa', fontSize: 14, lineHeight: 20 },
  curriculumHeader: { color: '#fff', fontSize: 18, fontWeight: 'bold', marginBottom: 16 },
  
  moduleCard: { marginBottom: 24 },
  moduleTitle: { color: '#a1a1aa', fontSize: 12, fontWeight: 'bold', textTransform: 'uppercase', marginBottom: 8, letterSpacing: 1 },
  lessonRow: { padding: 12, borderRadius: 8, marginBottom: 4 },
  activeLessonRow: { backgroundColor: 'rgba(250, 204, 21, 0.1)', borderWidth: 1, borderColor: 'rgba(250, 204, 21, 0.2)' },
  lockedLessonRow: { opacity: 0.6 },
  lessonText: { color: '#e4e4e7', fontSize: 15 },
  activeLessonText: { color: '#facc15', fontWeight: 'bold' },
  lockedLessonText: { color: '#71717a' }
});
