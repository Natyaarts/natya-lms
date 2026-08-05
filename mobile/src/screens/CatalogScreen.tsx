import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, FlatList, TouchableOpacity, StyleSheet, Image, ActivityIndicator, SafeAreaView, RefreshControl } from 'react-native';
import client from '../api/client';

export default function CatalogScreen({ navigation }: any) {
  const [courses, setCourses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchCatalogData = async () => {
    try {
      const res = await client.get('courses/');
      setCourses(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchCatalogData();
  }, []);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchCatalogData();
  }, []);

  const renderCourse = ({ item }: { item: any }) => {
    let thumbUrl = item.thumbnail || 'https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop';
    if (thumbUrl.startsWith('/')) {
      thumbUrl = `https://academy-api.natyaarts.com${thumbUrl}`;
    }

    return (
      <TouchableOpacity 
        style={styles.courseCard} 
        onPress={() => navigation.navigate('CourseDetails', { courseId: item.id })}
      >
        <Image 
          source={{ uri: thumbUrl }} 
          style={styles.thumbnail} 
        />
        <View style={styles.cardContent}>
          <Text style={styles.courseTitle}>{item.title}</Text>
          <Text style={styles.courseDescription} numberOfLines={2}>{item.description}</Text>
          <View style={styles.courseMetaContainer}>
            <Text style={styles.coursePrice}>₹{item.price}</Text>
            <Text style={styles.courseModules}>{item.modules?.length || 0} Modules</Text>
          </View>
        </View>
      </TouchableOpacity>
    );
  };

  if (loading) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator color="#facc15" size="large" />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <Image source={require('../../assets/icon.png')} style={{ width: 40, height: 40, resizeMode: 'contain' }} />
        <Text style={styles.headerTitle}>Course Catalog</Text>
      </View>
      
      <FlatList
        data={courses}
        keyExtractor={(item) => item.id.toString()}
        renderItem={renderCourse}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#facc15" />}
        contentContainerStyle={styles.listContainer}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyTitle}>No courses found</Text>
            <Text style={styles.emptyText}>Check back later for new masterclasses!</Text>
          </View>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#050505' },
  centered: { justifyContent: 'center', alignItems: 'center' },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 15, borderBottomWidth: 1, borderBottomColor: '#27272a' },
  headerTitle: { color: '#fff', fontSize: 20, fontWeight: 'bold', marginLeft: 16 },
  
  listContainer: { paddingBottom: 24, paddingTop: 16 },
  
  courseCard: { backgroundColor: '#0a0a0a', borderRadius: 16, overflow: 'hidden', marginBottom: 20, marginHorizontal: 16, borderWidth: 1, borderColor: '#27272a' },
  thumbnail: { width: '100%', height: 180, resizeMode: 'cover' },
  cardContent: { padding: 16 },
  courseTitle: { color: '#fff', fontSize: 20, fontWeight: 'bold', marginBottom: 8 },
  courseDescription: { color: '#a1a1aa', fontSize: 14, marginBottom: 12, lineHeight: 20 },
  courseMetaContainer: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  coursePrice: { color: '#facc15', fontSize: 18, fontWeight: 'bold' },
  courseModules: { color: '#a1a1aa', fontSize: 14, backgroundColor: '#18181b', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  
  emptyContainer: { padding: 24, alignItems: 'center', marginTop: 20 },
  emptyTitle: { color: '#fff', fontSize: 24, fontWeight: 'bold', marginBottom: 12 },
  emptyText: { color: '#a1a1aa', fontSize: 16, textAlign: 'center', lineHeight: 24 },
});
