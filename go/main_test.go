package main

import "testing"

func TestName(t *testing.T) {
	if Name == "" {
		t.Fatal("Name must not be empty")
	}
}
