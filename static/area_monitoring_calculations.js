(function (root, factory) {
  const calculations = factory();
  root.AreaMonitoringCalculations = calculations;
  if (typeof module === 'object' && module.exports) module.exports = calculations;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  function areaNumber(area) {
    const explicitId = Number(area?.areaId);
    if (Number.isInteger(explicitId) && explicitId > 0) return explicitId;
    const match = String(area?.name || '').match(/\d+/);
    return match ? Number(match[0]) : Number.MAX_SAFE_INTEGER;
  }

  function priorityArea(areas) {
    const candidates = (Array.isArray(areas) ? areas : []).filter(area => Number(area?.diseased) > 0);
    if (!candidates.length) return 'None';
    return [...candidates].sort((left, right) => Number(right.diseased) - Number(left.diseased)
      || areaNumber(left) - areaNumber(right))[0].name;
  }

  function totalDiseasedDetections(areas) {
    return (Array.isArray(areas) ? areas : []).reduce((total, area) => {
      const count = Number(area?.diseased);
      return total + (Number.isFinite(count) && count > 0 ? count : 0);
    }, 0);
  }

  function hasDiseasedDetection(detections, isHealthyClass) {
    return (Array.isArray(detections) ? detections : []).some(detection => !isHealthyClass(detection?.class));
  }

  function diseaseDetections(detections, isHealthyClass) {
    return (Array.isArray(detections) ? detections : []).reduce((found, detection) => {
      const label = String(detection?.class || detection?.disease_name || '').trim();
      if (label && !isHealthyClass(label)) found.push(label);
      return found;
    }, []);
  }

  function commonDisease(diseaseCounts) {
    const entries = Object.entries(diseaseCounts || {}).filter(([, count]) => Number(count) > 0);
    if (!entries.length) return 'No disease detected';
    return entries.sort((left, right) => Number(right[1]) - Number(left[1])
      || left[0].localeCompare(right[0]))[0][0];
  }

  function dashboardMetrics(areas) {
    return {
      totalDiseasedDetections: totalDiseasedDetections(areas),
      priorityArea: priorityArea(areas),
    };
  }

  return { commonDisease, dashboardMetrics, diseaseDetections, hasDiseasedDetection, priorityArea, totalDiseasedDetections };
}));
