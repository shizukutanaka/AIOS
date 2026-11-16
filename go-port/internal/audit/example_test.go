package audit_test

import (
	"fmt"

	"github.com/shizukutanaka/aios/internal/audit"
)

// ExampleVerify はディレクトリ内の全 audit log ファイルの hash chain を検証する。
//
// 戻り値の results は、ディレクトリにある日付ファイルそれぞれの検証結果。
// 1 つでも OK=false があれば、yagura verify subcommand は non-zero exit code を返す。
//
//	results, err := audit.Verify("/var/lib/yagura/audit")
//	for _, r := range results {
//	    fmt.Printf("%s: ok=%v records=%d\n", r.File, r.OK, r.TotalRecords)
//	}
func ExampleVerify() {
	// 実利用は yagura verify subcommand を参照(audit.Verify を呼ぶ)。
	_ = audit.Verify
	fmt.Println("see yagura verify subcommand")
	// Output: see yagura verify subcommand
}
