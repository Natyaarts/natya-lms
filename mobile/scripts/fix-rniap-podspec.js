const fs = require('fs');
const path = require('path');

const podspecPath = path.join(__dirname, '..', 'node_modules', 'react-native-iap', 'RNIap.podspec');

if (fs.existsSync(podspecPath)) {
  let content = fs.readFileSync(podspecPath, 'utf8');
  let changed = false;

  // Remove obsolete RCT-Folly dependency that fails pod install on RN 0.76+ / 0.85
  if (content.includes('s.dependency "RCT-Folly"')) {
    console.log('[fix-rniap-podspec] Removing obsolete RCT-Folly dependency from RNIap.podspec...');
    content = content.replace(/\s*s\.dependency\s+["']RCT-Folly["']/g, '');
    changed = true;
  }

  if (changed) {
    fs.writeFileSync(podspecPath, content, 'utf8');
    console.log('[fix-rniap-podspec] Successfully patched RNIap.podspec.');
  } else {
    console.log('[fix-rniap-podspec] RNIap.podspec already clean.');
  }
} else {
  console.log('[fix-rniap-podspec] RNIap.podspec not found at', podspecPath);
}
