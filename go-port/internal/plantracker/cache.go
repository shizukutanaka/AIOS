// plantracker cache helper: sha256 hash + JSON marshal for cache values.
package plantracker

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

// shortHash returns first 16 hex chars of sha256(content).
//
// 短縮 hash で十分: cache key 衝突確率 ~ 2^-64、portfolio 規模ではゼロ。
func shortHash(content string) string {
	h := sha256.Sum256([]byte(content))
	return hex.EncodeToString(h[:8]) // 16 chars
}

// marshalState encodes PlanState into JSON bytes for cache storage.
func marshalState(s PlanState) ([]byte, error) {
	return json.Marshal(s)
}

// unmarshalState decodes JSON bytes into PlanState.
func unmarshalState(raw []byte, s *PlanState) error {
	return json.Unmarshal(raw, s)
}
